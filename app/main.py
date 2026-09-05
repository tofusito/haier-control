from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.auth import require_scope
from app.automatic_auth import (
    AutomaticCredentialError,
    clear_direct_credentials,
    load_automatic_credentials,
)
from app.controller import Controller
from app.database import Database
from app.drivers.base import Driver, DriverError
from app.drivers.haier_auth import (
    HaierAuthenticationError,
    HaierLoginNotAccepted,
    HaierPairingTokenError,
    HaierProtocolError,
)
from app.drivers.haier_cloud import HaierCloudDriver
from app.drivers.mock import MockDriver
from app.events import EventBus
from app.logging_config import configure_logging
from app.models import (
    CommandRequest,
    CommandResult,
    DeviceView,
    HaierSetupOtp,
    HaierSetupResend,
    HaierSetupResponse,
    HaierSetupStart,
    HaierSetupStatusResponse,
    HealthResponse,
    TimerCreate,
    TimerUpdate,
    TimerView,
    TokenBootstrapRequest,
    TokenBootstrapResponse,
)
from app.rate_limit import RateLimiter
from app.runtime_config import load_runtime_config
from app.scheduler import TimerScheduler
from app.security import new_api_token, secret_matches, token_hash
from app.settings import Settings, load_secret
from app.setup_flow import SetupFlowManager
from app.trusted_access import (
    is_trusted_client,
    issue_session_cookie,
    require_trusted_network,
    validate_configuration,
)

STATIC_DIR = Path(__file__).parent / "static"
_LOGGER = logging.getLogger(__name__)


def _setup_failure(exc: Exception) -> tuple[int, str, str]:
    if isinstance(exc, HaierPairingTokenError):
        return (
            status.HTTP_400_BAD_REQUEST,
            "El token de emparejamiento no es válido o ya se usó. Solicita uno nuevo.",
            "pairing_token",
        )
    if isinstance(exc, HaierLoginNotAccepted):
        return (
            status.HTTP_400_BAD_REQUEST,
            "Salesforce ejecutó el login pero no devolvió el redirect esperado. Las "
            "credenciales pueden ser correctas; usa el fallback interactivo y revisa "
            "la compatibilidad del flujo antes de reintentar.",
            "login_not_accepted",
        )
    if isinstance(exc, HaierProtocolError):
        return (
            status.HTTP_502_BAD_GATEWAY,
            "hOn cambió temporalmente su flujo de acceso. No se enviaron las credenciales; "
            "actualiza Haier Control o inténtalo más tarde.",
            "protocol",
        )
    if isinstance(exc, httpx.HTTPError):
        return (
            status.HTTP_502_BAD_GATEWAY,
            "No se pudo contactar con hOn. Comprueba la conexión y vuelve a intentarlo.",
            "network",
        )
    if isinstance(exc, HaierAuthenticationError):
        return (
            status.HTTP_400_BAD_REQUEST,
            "hOn rechazó el acceso o devolvió una respuesta inesperada. Comprueba primero "
            "las credenciales en la app oficial y vuelve a intentarlo con un token nuevo.",
            "authentication",
        )
    return (
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "El acceso a hOn falló antes de completarse. Genera un token nuevo y vuelve a intentarlo.",
        "unexpected",
    )


def create_app(
    settings: Settings | None = None,
    *,
    master_key: bytes | None = None,
    bootstrap_secret: bytes | None = None,
    driver: Driver | None = None,
) -> FastAPI:
    config = load_runtime_config(settings or Settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(config.log_level)
        validate_configuration(config)
        key = master_key or load_secret(config.master_key_file)
        bootstrap = (
            bootstrap_secret
            if bootstrap_secret is not None
            else load_secret(config.bootstrap_token_file, required=False)
        )
        database = Database(config.database_path)
        await database.initialize()
        selected_driver: Driver
        if driver:
            selected_driver = driver
        elif config.driver == "haier-cloud":
            selected_driver = HaierCloudDriver(
                key,
                config.encrypted_session_file,
                config.haier_client_id,
            )
        else:
            selected_driver = MockDriver()
        events = EventBus()
        controller = Controller(selected_driver, database, events, config.command_dedupe_seconds)
        scheduler = TimerScheduler(database, controller, events)
        setup_manager = SetupFlowManager(
            selected_driver if isinstance(selected_driver, HaierCloudDriver) else None,
            database,
            key,
            config.haier_client_id,
            deliver_browser_token=not config.trusted_network_mode,
        )
        app.state.settings = config
        app.state.master_key = key
        app.state.bootstrap_secret = bootstrap
        app.state.database = database
        app.state.driver = selected_driver
        app.state.events = events
        app.state.controller = controller
        app.state.scheduler = scheduler
        app.state.rate_limiter = RateLimiter()
        app.state.setup_manager = setup_manager
        await selected_driver.start()
        if isinstance(selected_driver, HaierCloudDriver):
            automatic = None
            saved = None
            try:
                automatic = load_automatic_credentials(config)
                if automatic:
                    selected_driver.configure_credentials(automatic.email, automatic.password)
                if selected_driver.requires_reauth:
                    saved = selected_driver.saved_credentials()
                    if saved:
                        await setup_manager.begin_automatic(saved["email"], saved["password"])
            except AutomaticCredentialError as exc:
                detail = (
                    "La autenticación automática está mal configurada. Corrige ambos "
                    "secretos privados o usa el pairing manual."
                )
                setup_manager.set_automatic_failure(detail)
                _LOGGER.warning(
                    "Automatic hOn setup failed category=configuration exception=%s",
                    type(exc).__name__,
                )
            except Exception as exc:
                _error_status, detail, category = _setup_failure(exc)
                setup_manager.set_automatic_failure(detail)
                _LOGGER.warning(
                    "Automatic hOn setup failed category=%s exception=%s",
                    category,
                    type(exc).__name__,
                )
            finally:
                if automatic:
                    automatic.email = ""
                    automatic.password = ""
                if saved:
                    saved.clear()
                clear_direct_credentials(config)
        setup_manager.ensure_pairing()
        if config.trusted_network_mode and not setup_manager.setup_required:
            setup_manager.acknowledge_trusted_browser()
        await scheduler.start()
        yield
        await setup_manager.close()
        await scheduler.stop()
        await selected_driver.close()

    app = FastAPI(
        title="Haier Control API",
        version="0.1.0",
        description=(
            "Local API for capability-based Haier AC control and persistent timers. "
            "Bearer tokens protect the API by default; an explicit trusted home-network "
            "mode can provide a browser session without exposing secrets to the frontend."
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def no_store_setup(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/setup/haier") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", include_in_schema=False)
    async def index(request: Request) -> Response:
        require_trusted_network(request)
        response = FileResponse(STATIC_DIR / "index.html")
        issue_session_cookie(request, response)
        return response

    @app.get("/healthz", response_model=HealthResponse, tags=["system"])
    async def health(request: Request) -> HealthResponse:
        database_ok = await request.app.state.database.ping()
        scheduler_ok = bool(request.app.state.scheduler.healthy)
        selected_driver = request.app.state.driver
        degraded = bool(getattr(selected_driver, "last_error", None))
        setup_manager: SetupFlowManager = request.app.state.setup_manager
        setup_manager.ensure_pairing()
        return HealthResponse(
            status="ok" if database_ok and scheduler_ok and not degraded else "degraded",
            driver=selected_driver.name,
            database="ok" if database_ok else "error",
            scheduler="ok" if scheduler_ok else "error",
            setup_required=setup_manager.setup_required,
            trusted_network=is_trusted_client(request),
        )

    @app.post(
        "/api/v1/setup/haier/start",
        response_model=HaierSetupResponse,
        tags=["setup"],
    )
    async def start_haier_setup(request: Request, payload: HaierSetupStart) -> HaierSetupResponse:
        require_trusted_network(request)
        client = request.client.host if request.client else "unknown"
        if not await request.app.state.rate_limiter.allow(f"haier-setup:{client}", 5, 600):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Setup rate limit exceeded")
        try:
            manager = cast(SetupFlowManager, request.app.state.setup_manager)
            result = await manager.begin(payload.pairing_token, payload.email, payload.password)
            if config.trusted_network_mode and result.status == "complete":
                manager.acknowledge_trusted_browser()
                return result.model_copy(update={"api_token": None})
            return result
        except Exception as exc:
            error_status, detail, category = _setup_failure(exc)
            _LOGGER.warning(
                "Haier setup failed category=%s exception=%s detail=%s",
                category,
                type(exc).__name__,
                str(exc),
            )
            raise HTTPException(error_status, detail) from exc

    @app.get(
        "/api/v1/setup/haier/status",
        response_model=HaierSetupStatusResponse,
        tags=["setup"],
    )
    async def haier_setup_status(request: Request) -> HaierSetupStatusResponse:
        manager = cast(SetupFlowManager, request.app.state.setup_manager)
        return manager.automatic_status(expose_api_token=not config.trusted_network_mode)

    @app.post("/api/v1/setup/haier/ack", tags=["setup"])
    async def acknowledge_browser(
        request: Request, identity: Any = require_scope("read", limit=60)
    ) -> dict[str, bool]:
        manager = cast(SetupFlowManager, request.app.state.setup_manager)
        if identity.trusted_network:
            manager.acknowledge_trusted_browser()
        else:
            manager.acknowledge_browser(identity.digest)
        return {"acknowledged": True}

    @app.post(
        "/api/v1/setup/haier/otp",
        response_model=HaierSetupResponse,
        tags=["setup"],
    )
    async def submit_haier_otp(request: Request, payload: HaierSetupOtp) -> HaierSetupResponse:
        require_trusted_network(request)
        client = request.client.host if request.client else "unknown"
        if not await request.app.state.rate_limiter.allow(f"haier-otp:{client}", 8, 600):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "OTP rate limit exceeded")
        try:
            manager = cast(SetupFlowManager, request.app.state.setup_manager)
            result = await manager.submit_otp(payload.flow_id, payload.csrf_token, payload.code)
            if config.trusted_network_mode and result.status == "complete":
                manager.acknowledge_trusted_browser()
                return result.model_copy(update={"api_token": None})
            return result
        except Exception as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "OTP was rejected, expired, or the setup flow is no longer valid",
            ) from exc

    @app.post(
        "/api/v1/setup/haier/resend",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["setup"],
    )
    async def resend_haier_otp(request: Request, payload: HaierSetupResend) -> None:
        require_trusted_network(request)
        client = request.client.host if request.client else "unknown"
        if not await request.app.state.rate_limiter.allow(f"haier-resend:{client}", 3, 600):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Resend rate limit exceeded")
        try:
            await request.app.state.setup_manager.resend(payload.flow_id, payload.csrf_token)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "The verification code could not be resent"
            ) from exc

    @app.post(
        "/api/v1/setup/tokens",
        response_model=TokenBootstrapResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["setup"],
    )
    async def bootstrap_token(
        request: Request,
        payload: TokenBootstrapRequest,
        secret: Annotated[str | None, Header(alias="X-Bootstrap-Token")] = None,
    ) -> TokenBootstrapResponse:
        database: Database = request.app.state.database
        if await database.token_count() > 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "Bootstrap is already complete")
        expected: bytes = request.app.state.bootstrap_secret
        if not expected or not secret or not secret_matches(secret, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bootstrap token")
        token = new_api_token()
        await database.create_token(
            payload.name, token_hash(token, request.app.state.master_key), set(payload.scopes)
        )
        return TokenBootstrapResponse(token=token, name=payload.name, scopes=sorted(payload.scopes))

    @app.get("/api/v1/openapi.json", tags=["system"])
    async def openapi_document(_identity: Any = require_scope("read", limit=60)) -> dict[str, Any]:
        return app.openapi()

    @app.get("/api/v1/devices", response_model=list[DeviceView], tags=["devices"])
    async def list_devices(
        request: Request, _identity: Any = require_scope("read", limit=60)
    ) -> list[DeviceView]:
        try:
            controller = cast(Controller, request.app.state.controller)
            return await controller.list_devices()
        except DriverError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    @app.get("/api/v1/devices/{device_id}", response_model=DeviceView, tags=["devices"])
    async def get_device(
        request: Request,
        device_id: str,
        _identity: Any = require_scope("read", limit=60),
    ) -> DeviceView:
        try:
            controller = cast(Controller, request.app.state.controller)
            return await controller.get_device(device_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found") from exc
        except DriverError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    @app.post(
        "/api/v1/devices/{device_id}/commands",
        response_model=CommandResult,
        tags=["devices"],
    )
    async def command_device(
        request: Request,
        device_id: str,
        payload: CommandRequest,
        _identity: Any = require_scope("control", limit=12),
    ) -> CommandResult:
        try:
            controller = cast(Controller, request.app.state.controller)
            return await controller.command(device_id, payload)
        except DriverError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    @app.get("/api/v1/timers", response_model=list[TimerView], tags=["timers"])
    async def list_timers(
        request: Request,
        include_finished: bool = Query(default=True),
        _identity: Any = require_scope("timers", limit=60),
    ) -> list[TimerView]:
        database = cast(Database, request.app.state.database)
        return await database.list_timers(include_finished=include_finished)

    @app.post(
        "/api/v1/timers",
        response_model=TimerView,
        status_code=status.HTTP_201_CREATED,
        tags=["timers"],
    )
    async def create_timer(
        request: Request,
        payload: TimerCreate,
        _identity: Any = require_scope("timers", limit=30),
    ) -> TimerView:
        if payload.execute_at <= datetime.now(UTC):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "execute_at must be future")
        try:
            controller = cast(Controller, request.app.state.controller)
            database = cast(Database, request.app.state.database)
            await controller.get_device(payload.device_id)
            timer = await database.create_timer(payload)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Device not found") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        await request.app.state.events.publish("timer", timer.model_dump(mode="json"))
        return timer

    @app.patch("/api/v1/timers/{timer_id}", response_model=TimerView, tags=["timers"])
    async def update_timer(
        request: Request,
        timer_id: str,
        payload: TimerUpdate,
        _identity: Any = require_scope("timers", limit=30),
    ) -> TimerView:
        if payload.execute_at and payload.execute_at <= datetime.now(UTC):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "execute_at must be future")
        database = cast(Database, request.app.state.database)
        timer = await database.update_timer(timer_id, payload)
        if not timer:
            raise HTTPException(status.HTTP_409_CONFLICT, "Timer is missing or no longer editable")
        await request.app.state.events.publish("timer", timer.model_dump(mode="json"))
        return timer

    @app.delete("/api/v1/timers/{timer_id}", response_model=TimerView, tags=["timers"])
    async def cancel_timer(
        request: Request,
        timer_id: str,
        _identity: Any = require_scope("timers", limit=30),
    ) -> TimerView:
        database = cast(Database, request.app.state.database)
        timer = await database.cancel_timer(timer_id)
        if not timer:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Timer is missing or no longer cancellable"
            )
        await request.app.state.events.publish("timer", timer.model_dump(mode="json"))
        return timer

    @app.get("/api/v1/events", tags=["events"])
    async def events(
        request: Request, _identity: Any = require_scope("read", limit=10)
    ) -> StreamingResponse:
        return StreamingResponse(
            request.app.state.events.stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
