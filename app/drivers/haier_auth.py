from __future__ import annotations

import html
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit

import httpx

AUTH_API = "https://account2.hon-smarthome.com"
API_URL = "https://api-iot.he.services"
REDIRECT_URI = "hon://mobilesdk/detect/oauth/done"
OAUTH_SCOPE = "api openid refresh_token web"
AUTH_USER_AGENT = "Chrome/999.999.999.999"


class HaierAuthenticationError(RuntimeError):
    pass


class HaierPairingTokenError(HaierAuthenticationError):
    pass


class HaierLoginNotAccepted(HaierAuthenticationError):
    pass


class HaierProtocolError(HaierAuthenticationError):
    pass


class HaierMfaRequired(HaierAuthenticationError):
    pass


@dataclass
class HaierTokens:
    access_token: str
    refresh_token: str
    id_token: str
    cognito_token: str
    mobile_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "cognito_token": self.cognito_token,
            "mobile_id": self.mobile_id,
        }


@dataclass(repr=False)
class MfaContext:
    host: str
    referer: str
    vid: str
    verify: dict[str, Any]
    resend: dict[str, Any]
    form_action: str
    hidden_fields: dict[str, str]
    finish_marker: str
    brand: str
    locale: str | None


def _remote_descriptor(page: str, method: str) -> dict[str, Any]:
    match = re.search(r'\{"name":"' + re.escape(method) + r'"[^{}]*\}', page)
    try:
        raw = json.loads(match.group(0)) if match else {}
    except (json.JSONDecodeError, ValueError):
        raw = {}
    return {
        "method": method,
        "csrf": raw.get("csrf", ""),
        "authorization": raw.get("authorization", ""),
        "ns": raw.get("ns", ""),
        "ver": int(float(raw.get("ver", 45))),
    }


def _parse_mfa_context(page: str, page_url: str) -> MfaContext:
    lowered = page.lower()
    if not all(marker in lowered for marker in ("progressivelogincontroller", "verifyemailotp")):
        raise HaierAuthenticationError("Progressive login page is not an email OTP challenge")
    if not re.search(r"name\s*=\s*['\"]emailcode['\"]", page, re.I):
        raise HaierAuthenticationError("OTP input was not found")
    verify = _remote_descriptor(page, "verifyEmailOTP")
    resend = _remote_descriptor(page, "resendEmailCode")
    if not verify["csrf"] or not verify["authorization"] or not resend["csrf"]:
        raise HaierAuthenticationError("OTP remoting credentials were not found")
    vid_match = re.search(r'"vid":"([^"]+)"', page)
    if not vid_match:
        raise HaierAuthenticationError("OTP ViewState id was not found")
    hidden: dict[str, str] = {}
    for tag in re.findall(r'<input\b[^>]*type="hidden"[^>]*>', page, re.I):
        attrs = {
            key.lower(): html.unescape(value)
            for key, value in re.findall(r'(\w[\w:.\-]*)\s*=\s*"([^"]*)"', tag)
        }
        if "name" in attrs:
            hidden[attrs["name"]] = attrs.get("value", "")
    jsf = re.search(r"jsfcljs\(document\.forms\['([^']+)'\],'([^']+)'", page)
    form_name = jsf.group(1) if jsf else "ProgressiveLogin:j_id8"
    finish_marker = (
        jsf.group(2).split(",")[0] if jsf else "ProgressiveLogin:j_id8:j_id12"
    )
    form = re.search(r'<form\b[^>]*name="' + re.escape(form_name) + r'"[^>]*>', page)
    action = "/ProgressiveLogin"
    if form:
        attributes = dict(re.findall(r'(\w[\w:.\-]*)\s*=\s*"([^"]*)"', form.group(0)))
        action = attributes.get("action", action)
    parsed = urlsplit(page_url)
    host = f"https://{parsed.netloc or urlsplit(AUTH_API).netloc}"
    brand = next(
        (
            name
            for name in ("Haier", "Candy", "Hoover", "Rosieres")
            if name.lower() in page_url.lower()
        ),
        "SmartHome",
    )
    return MfaContext(
        host=host,
        referer=page_url,
        vid=vid_match.group(1),
        verify=verify,
        resend=resend,
        form_action=urljoin(f"{host}/", action),
        hidden_fields=hidden,
        finish_marker=finish_marker,
        brand=brand,
        locale=parse_qs(parsed.query).get("locale", [None])[0],
    )


def device_payload(mobile_id: str, *, mobile: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "appVersion": "2.27.9",
        "mobileId": mobile_id,
        "osVersion": 34,
        "deviceModel": "haier-control",
    }
    payload["mobileOs" if mobile else "os"] = "android"
    return payload


def _safe_auth_url(value: str) -> str:
    resolved = urljoin(AUTH_API, value)
    parsed = urlsplit(resolved)
    if parsed.scheme not in {"http", "https"}:
        return resolved
    if parsed.netloc != urlsplit(AUTH_API).netloc:
        demoted = "/" + (parsed.netloc + parsed.path).lstrip("/")
        return urlunsplit(("https", urlsplit(AUTH_API).netloc, demoted, parsed.query, ""))
    return resolved


def _first_navigation_target(page: str) -> str | None:
    match = re.search(r"(?:url|href)\s*=\s*['\"](.+?)['\"]", page)
    return match.group(1) if match else None


def _parse_tokens(value: str) -> dict[str, str]:
    marker = value.find("oauth/done#")
    source = value[marker:] if marker >= 0 else value
    result: dict[str, str] = {}
    for key in ("access_token", "refresh_token", "id_token"):
        match = re.search(rf"(?:^|[#?&]){key}=([^&\s'\"<>]+)", source)
        if match:
            result[key] = unquote(match.group(1)) if key == "refresh_token" else match.group(1)
    return result


def _aura_response_shape(payload: Any) -> dict[str, Any]:
    """Structural, redaction-safe summary of an Aura response for diagnostics.

    Never includes field values (username/password/tokens/HTML), only types,
    lengths and the action state, so it is safe to log.
    """
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    shape: dict[str, Any] = {"top_keys": sorted(payload.keys())}
    events = payload.get("events")
    shape["event_count"] = len(events) if isinstance(events, list) else None
    actions = payload.get("actions")
    if isinstance(actions, list) and actions and isinstance(actions[0], dict):
        action = actions[0]
        shape["action_state"] = action.get("state")
        returned = action.get("returnValue")
        shape["return_type"] = type(returned).__name__
        shape["return_length"] = len(returned) if isinstance(returned, str) else None
    else:
        shape["action_count"] = len(actions) if isinstance(actions, list) else None
    shape["has_error"] = payload.get("error") is not None
    return shape


def _aura_redirect(payload: Any) -> str:
    """Extract a successful Aura handoff without exposing rejection text."""
    if not isinstance(payload, dict):
        raise HaierProtocolError("Aura login response was not an object")
    try:
        redirect = payload["events"][0]["attributes"]["values"]["url"]
    except (KeyError, IndexError, TypeError):
        redirect = None
    if isinstance(redirect, str) and redirect:
        return redirect

    actions = payload.get("actions")
    if isinstance(actions, list) and actions and isinstance(actions[0], dict):
        returned = actions[0].get("returnValue")
        if isinstance(returned, str) and returned.startswith(("/", "http://", "https://")):
            return returned
        lowered = returned.lower() if isinstance(returned, str) else ""
        if "username" in lowered and "password" in lowered:
            raise HaierLoginNotAccepted("Salesforce did not return a login redirect")
    shape = _aura_response_shape(payload)
    raise HaierProtocolError(
        f"Aura login response did not include a safe redirect (shape={shape})"
    )


def _login_payload(
    email: str,
    password: str,
    fwuid: str,
    loaded: dict[str, Any],
    page_uri: str,
) -> tuple[str, dict[str, int]]:
    start_url = unquote(page_uri.rsplit("startURL=", 1)[-1]).split("%3D")[0]
    action = {
        "id": "79;a",
        "descriptor": "apex://LightningLoginCustomController/ACTION$login",
        "callingDescriptor": "markup://c:loginForm",
        "params": {"username": email, "password": password, "startUrl": start_url},
    }
    form: dict[str, Any] = {
        "message": {"actions": [action]},
        "aura.context": {
            "mode": "PROD",
            "fwuid": fwuid,
            "app": "siteforce:loginApp2",
            "loaded": loaded,
            "dn": [],
            "globals": {},
            "uad": False,
        },
        "aura.pageURI": page_uri,
        "aura.token": None,
    }
    body = "&".join(f"{key}={quote(json.dumps(value))}" for key, value in form.items())
    return body, {"r": 3, "other.LightningLoginCustom.login": 1}


class HaierAuthenticator:
    """Independent, minimal implementation of the documented Salesforce flow.

    It intentionally stops at an email-OTP page. MFA verification is not implemented in
    v0.1, so the user gets an explicit limitation instead of a false login success.
    """

    def __init__(self, client_id: str, timeout: float = 20.0) -> None:
        self.client_id = client_id
        self.timeout = timeout

    def _authorize_url(self) -> str:
        parts = {
            "response_type": "token+id_token",
            "client_id": self.client_id,
            "redirect_uri": quote(REDIRECT_URI),
            "display": "touch",
            "scope": OAUTH_SCOPE,
            "nonce": str(uuid.uuid4()),
        }
        return f"{AUTH_API}/services/oauth2/authorize/expid_Login?" + "&".join(
            f"{key}={value}" for key, value in parts.items()
        )

    async def login(self, email: str, password: str) -> HaierTokens:
        headers = {"user-agent": AUTH_USER_AGENT}
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, headers=headers
        ) as client:
            introduction = await client.get(self._authorize_url())
            introduction.raise_for_status()
            tokens = _parse_tokens(introduction.text)
            if len(tokens) == 3:
                return await self._exchange(client, tokens)

            login_url = _first_navigation_target(introduction.text)
            if not login_url:
                raise HaierAuthenticationError("The login page did not expose a safe next step")
            if login_url.startswith("/NewhOnLogin"):
                login_url = f"{AUTH_API}/s/login{login_url}"

            target = _safe_auth_url(login_url)
            for _ in range(2):
                response = await client.get(target, follow_redirects=False)
                target = _safe_auth_url(response.headers.get("location", target))
            separator = "&" if "?" in target else "?"
            target += f"{separator}System=IoT_Mobile_App&RegistrationSubChannel=hOn"
            login_page = await client.get(target)
            login_page.raise_for_status()

            match = re.search(r'"fwuid":"(.*?)","loaded":(\{.*?\})', login_page.text)
            if not match:
                raise HaierProtocolError("Salesforce login schema changed (fwuid missing)")
            fwuid, loaded_raw = match.groups()
            loaded = json.loads(loaded_raw)
            page_uri = str(login_page.url).replace(AUTH_API, "")
            encoded, params = _login_payload(email, password, fwuid, loaded, page_uri)
            login = await client.post(
                f"{AUTH_API}/s/sfsites/aura",
                params=params,
                content=encoded,
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
            login.raise_for_status()
            try:
                redirect = _aura_redirect(login.json())
            except json.JSONDecodeError as exc:
                raise HaierProtocolError("Aura login response was unreadable") from exc

            handoff = await client.get(_safe_auth_url(str(redirect)))
            handoff.raise_for_status()
            href = _first_navigation_target(handoff.text)
            if href and "ProgressiveLogin" in href:
                raise HaierMfaRequired(
                    "Email two-factor authentication is detected but not implemented in v0.1"
                )
            if not href:
                raise HaierAuthenticationError("OAuth handoff did not include a token page")
            token_page = await client.get(_safe_auth_url(href))
            token_page.raise_for_status()
            tokens = _parse_tokens(token_page.text)
            if len(tokens) != 3:
                raise HaierAuthenticationError("OAuth token handoff was incomplete")
            return await self._exchange(client, tokens)

    async def _exchange(self, client: httpx.AsyncClient, tokens: dict[str, str]) -> HaierTokens:
        mobile_id = f"haier-control-{uuid.uuid4()}"
        response = await client.post(
            f"{API_URL}/auth/v1/login",
            headers={"id-token": tokens["id_token"]},
            json=device_payload(mobile_id),
        )
        response.raise_for_status()
        cognito = response.json().get("cognitoUser", {}).get("Token")
        if not isinstance(cognito, str) or not cognito:
            raise HaierAuthenticationError("Cognito exchange returned no token")
        return HaierTokens(
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            id_token=tokens["id_token"],
            cognito_token=cognito,
            mobile_id=mobile_id,
        )

    async def refresh(self, refresh_token: str, mobile_id: str) -> HaierTokens:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{AUTH_API}/services/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"user-agent": AUTH_USER_AGENT},
            )
            response.raise_for_status()
            data = response.json()
            access = data.get("access_token")
            identity = data.get("id_token")
            if not isinstance(access, str) or not isinstance(identity, str):
                raise HaierAuthenticationError("Refresh response was incomplete")
            exchange = await client.post(
                f"{API_URL}/auth/v1/login",
                headers={"id-token": identity},
                json=device_payload(mobile_id),
            )
            exchange.raise_for_status()
            cognito = exchange.json().get("cognitoUser", {}).get("Token")
            if not isinstance(cognito, str) or not cognito:
                raise HaierAuthenticationError("Cognito refresh returned no token")
            return HaierTokens(
                access_token=access,
                refresh_token=str(data.get("refresh_token") or refresh_token),
                id_token=identity,
                cognito_token=cognito,
                mobile_id=mobile_id,
            )


class InteractiveHaierLogin:
    """A short-lived login session that can pause for a Salesforce email OTP."""

    def __init__(self, client_id: str, timeout: float = 20.0) -> None:
        self.authenticator = HaierAuthenticator(client_id, timeout)
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"user-agent": AUTH_USER_AGENT},
        )
        self.context: MfaContext | None = None
        self.closed = False

    async def begin(self, email: str, password: str) -> HaierTokens | None:
        introduction = await self.client.get(self.authenticator._authorize_url())
        introduction.raise_for_status()
        tokens = _parse_tokens(introduction.text)
        if len(tokens) == 3:
            return await self.authenticator._exchange(self.client, tokens)
        login_url = _first_navigation_target(introduction.text)
        if not login_url:
            raise HaierAuthenticationError("The login page did not expose a safe next step")
        if login_url.startswith("/NewhOnLogin"):
            login_url = f"{AUTH_API}/s/login{login_url}"
        target = _safe_auth_url(login_url)
        for _ in range(2):
            response = await self.client.get(target, follow_redirects=False)
            target = _safe_auth_url(response.headers.get("location", target))
        separator = "&" if "?" in target else "?"
        target += f"{separator}System=IoT_Mobile_App&RegistrationSubChannel=hOn"
        login_page = await self.client.get(target)
        login_page.raise_for_status()
        match = re.search(r'"fwuid":"(.*?)","loaded":(\{.*?\})', login_page.text)
        if not match:
            raise HaierProtocolError("Salesforce login schema changed (fwuid missing)")
        fwuid, loaded_raw = match.groups()
        page_uri = str(login_page.url).replace(AUTH_API, "")
        encoded, params = _login_payload(
            email, password, fwuid, json.loads(loaded_raw), page_uri
        )
        login = await self.client.post(
            f"{AUTH_API}/s/sfsites/aura",
            params=params,
            content=encoded,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        login.raise_for_status()
        try:
            redirect = _aura_redirect(login.json())
        except json.JSONDecodeError as exc:
            raise HaierProtocolError("Aura login response was unreadable") from exc
        handoff = await self.client.get(_safe_auth_url(str(redirect)))
        handoff.raise_for_status()
        href = _first_navigation_target(handoff.text)
        if href and "ProgressiveLogin" in href:
            progressive = await self.client.get(_safe_auth_url(href))
            progressive.raise_for_status()
            self.context = _parse_mfa_context(progressive.text, str(progressive.url))
            await self.resend_code()
            return None
        if not href:
            raise HaierAuthenticationError("OAuth handoff did not include a token page")
        token_page = await self.client.get(_safe_auth_url(href))
        token_page.raise_for_status()
        tokens = _parse_tokens(token_page.text)
        if len(tokens) != 3:
            raise HaierAuthenticationError("OAuth token handoff was incomplete")
        return await self.authenticator._exchange(self.client, tokens)

    async def resend_code(self) -> None:
        if not self.context:
            raise HaierAuthenticationError("There is no active OTP challenge")
        entry = await self._remoting(
            self.context.resend,
            [{"expid": self.context.brand, "localeId": self.context.locale}],
            11,
        )
        if entry.get("result") is not True:
            raise HaierAuthenticationError("The verification email could not be sent")

    async def submit_code(self, code: str) -> HaierTokens:
        if not self.context:
            raise HaierAuthenticationError("There is no active OTP challenge")
        entry = await self._remoting(self.context.verify, [code], 21)
        if entry.get("result") is not True:
            if entry.get("type") == "exception" or int(entry.get("statusCode") or 0) >= 500:
                raise HaierAuthenticationError("The OTP service is temporarily unavailable")
            raise HaierAuthenticationError("The verification code is invalid or expired")
        finish_body = dict(self.context.hidden_fields)
        finish_body[self.context.finish_marker] = self.context.finish_marker
        finish = await self.client.post(
            self.context.form_action,
            data=finish_body,
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "referer": self.context.referer,
            },
        )
        finish.raise_for_status()
        resume = await self.client.get(self.authenticator._authorize_url())
        resume.raise_for_status()
        target = _first_navigation_target(resume.text)
        tokens = _parse_tokens(target or resume.text)
        if len(tokens) != 3:
            raise HaierAuthenticationError("OTP succeeded but OAuth tokens were incomplete")
        return await self.authenticator._exchange(self.client, tokens)

    async def _remoting(
        self, descriptor: dict[str, Any], data: list[Any], tid: int
    ) -> dict[str, Any]:
        assert self.context is not None
        payload = {
            "action": "ProgressiveLoginController",
            "method": descriptor["method"],
            "data": data,
            "type": "rpc",
            "tid": tid,
            "ctx": {
                "csrf": descriptor["csrf"],
                "vid": self.context.vid,
                "ns": descriptor["ns"],
                "ver": descriptor["ver"],
                "authorization": descriptor["authorization"],
            },
        }
        response = await self.client.post(
            f"{self.context.host}/apexremote",
            json=payload,
            headers={
                "content-type": "application/json",
                "x-user-agent": "Visualforce-Remoting",
                "referer": self.context.referer,
            },
        )
        response.raise_for_status()
        try:
            parsed = response.json()
        except json.JSONDecodeError as exc:
            raise HaierAuthenticationError("OTP response was unreadable") from exc
        if isinstance(parsed, list):
            return parsed[0] if parsed and isinstance(parsed[0], dict) else {}
        return parsed if isinstance(parsed, dict) else {}

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self.client.aclose()
