(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.HaierUiState = factory();
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const DEFAULT_TIMEOUT_MS = 10000;
  const DEFAULT_SETTLE_MS = 4000;

  function clone(value) { return JSON.parse(JSON.stringify(value)); }

  function applyOptimisticState(device, operation, value, key) {
    switch (operation) {
      case "power": device.state.power = Boolean(value); break;
      case "set_mode": device.state.mode = value; break;
      case "set_temperature": device.state.target_temperature = Number(value); break;
      case "set_fan": device.state.fan_mode = value; break;
      case "set_vertical_swing": device.state.vertical_swing = value; break;
      case "set_horizontal_swing": device.state.horizontal_swing = value; break;
      case "set_advanced": device.state.advanced = { ...device.state.advanced, [key]: Boolean(value) }; break;
    }
    device.state.updated_at = new Date().toISOString();
    device.state.stale = false;
  }

  function commandMatches(device, command) {
    const { operation, value, key } = command;
    const current = device.state;
    switch (operation) {
      case "power": return current.power === Boolean(value);
      case "set_mode": return current.mode === value;
      case "set_temperature": return Number(current.target_temperature) === Number(value);
      case "set_fan": return current.fan_mode === value;
      case "set_vertical_swing": return current.vertical_swing === value;
      case "set_horizontal_swing": return current.horizontal_swing === value;
      case "set_advanced": return Boolean(current.advanced?.[key]) === Boolean(value);
      default: return false;
    }
  }

  function presentPowerState(device, pending) {
    const on = Boolean(device.state.power);
    const mode = device.state.mode || "auto";
    return {
      pressed: on,
      label: on ? "Encendido" : "Apagado",
      ariaLabel: on ? `Apagar ${device.name}` : `Encender ${device.name}`,
      caption: `${on ? "Encendido" : "Apagado"} · ${mode}`,
      status: pending ? "Sincronizando…" : "",
    };
  }

  // The single place that decides whether this browser still needs a local API
  // token. Both the boot path and every refresh ask here: when the rule lived in
  // two places, trusted-network mode booted correctly and was then immediately
  // sent back to the token dialog by the copy that had not been updated.
  function needsLocalToken(session) {
    return !(session && (session.token || session.trustedNetwork));
  }

  function mergeTimerMutations(remoteTimers, mutations) {
    const merged = new Map((remoteTimers || []).map(timer => [timer.id, timer]));
    const remoteByKey = new Map((remoteTimers || []).map(timer => [timer.idempotency_key, timer.id]));
    mutations?.forEach(mutation => {
      const optimistic = mutation.optimistic;
      const remoteId = remoteByKey.get(optimistic.idempotency_key);
      if (remoteId) merged.delete(remoteId);
      merged.set(optimistic.id, optimistic);
    });
    return [...merged.values()].sort((a, b) => new Date(a.execute_at) - new Date(b.execute_at));
  }

  function createCommandController(options) {
    const {
      state,
      api,
      render = () => {},
      showError = () => {},
      toast = () => {},
      queueRefresh = () => {},
      now = () => Date.now(),
      setTimer = setTimeout,
      clearTimer = clearTimeout,
      timeoutMs = DEFAULT_TIMEOUT_MS,
      settleMs = DEFAULT_SETTLE_MS,
    } = options;
    state.pendingCommands ||= new Set();
    state.optimisticSnapshots ||= new Map();
    state.acceptedCommands ||= new Map();

    function restore(device) {
      const snapshot = state.optimisticSnapshots.get(device.id);
      if (snapshot) device.state = snapshot;
      state.optimisticSnapshots.delete(device.id);
      state.pendingCommands.delete(device.id);
      render();
    }

    async function send(deviceId, operation, value, key = null) {
      const device = state.devices.find(item => item.id === deviceId);
      if (!device || state.pendingCommands.has(deviceId)) return { status: "ignored" };
      if (!state.optimisticSnapshots.has(deviceId)) {
        state.optimisticSnapshots.set(deviceId, clone(device.state));
      }
      applyOptimisticState(device, operation, value, key);
      state.pendingCommands.add(deviceId);
      render();

      const abortController = typeof AbortController === "function" ? new AbortController() : null;
      let timedOut = false;
      let rejectTimeout;
      const timeoutPromise = new Promise((_, reject) => { rejectTimeout = reject; });
      const timeoutId = setTimer(() => {
        timedOut = true;
        abortController?.abort();
        const timeout = new Error("request-timeout"); timeout.name = "AbortError"; rejectTimeout(timeout);
      }, timeoutMs);
      try {
        const request = api(`/api/v1/devices/${deviceId}/commands`, {
          method: "POST",
          body: JSON.stringify({ operation, value, key }),
          ...(abortController ? { signal: abortController.signal } : {}),
        });
        const result = await Promise.race([request, timeoutPromise]);
        if (timedOut) {
          const timeout = new Error("request-timeout"); timeout.name = "AbortError"; throw timeout;
        }
        if (!result?.accepted) {
          restore(device);
          toast(result?.message || "El cambio no fue aceptado");
          return { status: "rejected", result };
        }
        if (result.state) device.state = result.state;
        // Keep the acceptance marker even when the API includes a state. A
        // following SSE/poll can still be older than that response, so it must
        // not repaint the card until the short settle window has elapsed.
        state.acceptedCommands.set(deviceId, { operation, value, key, acceptedAt: now() });
        state.optimisticSnapshots.delete(deviceId);
        state.pendingCommands.delete(deviceId);
        showError(""); render();
        toast("Cambio confirmado");
        queueRefresh(1200);
        return { status: "accepted", result };
      } catch (error) {
        restore(device);
        if (timedOut || error?.name === "AbortError") {
          showError("No se pudo confirmar el cambio. Puedes reintentarlo.");
          toast("Sin confirmar · puedes reintentar");
          queueRefresh(1200);
          return { status: "timeout", error };
        }
        const detail = error?.message || "No se pudo aplicar el cambio";
        showError(`${detail}. Puedes reintentarlo.`);
        toast("No se pudo aplicar · reintenta");
        return { status: "failed", error };
      } finally {
        clearTimer(timeoutId);
      }
    }

    function reconcileDevices(remoteDevices) {
      const previous = new Map(state.devices.map(device => [device.id, device]));
      const timestamp = now();
      state.devices = remoteDevices.map(device => {
        const oldDevice = previous.get(device.id);
        if (state.pendingCommands.has(device.id)) return oldDevice || device;
        const accepted = state.acceptedCommands.get(device.id);
        if (!accepted) return device;
        if (commandMatches(device, accepted)) {
          state.acceptedCommands.delete(device.id);
          return device;
        }
        if (timestamp - accepted.acceptedAt < settleMs) return oldDevice || device;
        state.acceptedCommands.delete(device.id);
        return device;
      });
      return state.devices;
    }

    return { send, reconcileDevices, commandMatches, applyOptimisticState, presentPowerState, mergeTimerMutations, needsLocalToken };
  }

  return { createCommandController, applyOptimisticState, commandMatches, presentPowerState, mergeTimerMutations, needsLocalToken };
}));
