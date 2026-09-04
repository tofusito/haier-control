const test = require("node:test");
const assert = require("node:assert/strict");

const {
  createCommandController,
  mergeTimerMutations,
  presentPowerState,
} = require("../app/static/ui-state.js");

function device(state = {}) {
  return {
    id: "salon",
    name: "AC Salón",
    state: {
      power: false,
      mode: "cool",
      target_temperature: 24,
      fan_mode: "auto",
      vertical_swing: "position_1",
      horizontal_swing: "position_1",
      advanced: { quiet: false },
      updated_at: "2026-09-05T00:00:00.000Z",
      stale: false,
      ...state,
    },
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((fulfil, fail) => { resolve = fulfil; reject = fail; });
  return { promise, resolve, reject };
}

function manualTimer() {
  let callback;
  return {
    setTimer(fn) { callback = fn; return 1; },
    clearTimer() {},
    fire() { callback?.(); },
  };
}

function harness({ api, now = () => 100, timeoutMs } = {}) {
  const timer = manualTimer();
  const state = { devices: [device()] };
  const renders = [];
  const errors = [];
  const toasts = [];
  const refreshes = [];
  const controller = createCommandController({
    state,
    api: api || (async () => ({ accepted: true, state: null })),
    render: () => renders.push(state.devices[0].state.power),
    showError: message => errors.push(message),
    toast: message => toasts.push(message),
    queueRefresh: delay => refreshes.push(delay),
    now,
    setTimer: timer.setTimer,
    clearTimer: timer.clearTimer,
    ...(timeoutMs ? { timeoutMs } : {}),
  });
  return { state, controller, timer, renders, errors, toasts, refreshes };
}

test("power is reflected immediately and consolidates after acceptance", async () => {
  const pending = deferred();
  const calls = [];
  const h = harness({ api: async (path, options) => { calls.push({ path, options }); return pending.promise; } });

  const resultPromise = h.controller.send("salon", "power", true);
  assert.equal(h.state.devices[0].state.power, true);
  assert.equal(h.state.pendingCommands.has("salon"), true);
  assert.deepEqual(presentPowerState(h.state.devices[0], true), {
    pressed: true,
    label: "Encendido",
    ariaLabel: "Apagar AC Salón",
    caption: "Encendido · cool",
    status: "Sincronizando…",
  });
  assert.equal(calls.length, 1);
  assert.match(calls[0].options.body, /"operation":"power"/);

  pending.resolve({ accepted: true, state: null });
  const result = await resultPromise;
  assert.equal(result.status, "accepted");
  assert.equal(h.state.pendingCommands.size, 0);
  assert.equal(h.state.devices[0].state.power, true);
  assert.deepEqual(h.toasts, ["Cambio confirmado"]);
  assert.deepEqual(h.refreshes, [1200]);
});

test("a rejected command rolls back and leaves a clear retry path", async () => {
  const h = harness({ api: async () => { throw new Error("Haier no aceptó el cambio"); } });

  const result = await h.controller.send("salon", "power", true);
  assert.equal(result.status, "failed");
  assert.equal(h.state.devices[0].state.power, false);
  assert.equal(h.state.pendingCommands.size, 0);
  assert.match(h.errors[0], /reintentarlo/);
  assert.deepEqual(h.toasts, ["No se pudo aplicar · reintenta"]);
});

test("a timeout aborts the request, rolls back, and re-enables retry", async () => {
  const h = harness({
    timeoutMs: 50,
    api: async (_path, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => {
        const error = new Error("aborted"); error.name = "AbortError"; reject(error);
      });
    }),
  });
  const resultPromise = h.controller.send("salon", "power", true);
  assert.equal(h.state.devices[0].state.power, true);
  h.timer.fire();
  const result = await resultPromise;
  assert.equal(result.status, "timeout");
  assert.equal(h.state.devices[0].state.power, false);
  assert.equal(h.state.pendingCommands.size, 0);
  assert.match(h.errors[0], /reintentarlo/);
  assert.deepEqual(h.refreshes, [1200]);
});

test("a late success after the timeout cannot resurrect the optimistic state", async () => {
  const pending = deferred();
  const h = harness({ timeoutMs: 50, api: async () => pending.promise });
  const resultPromise = h.controller.send("salon", "power", true);
  h.timer.fire();
  pending.resolve({ accepted: true, state: null });
  const result = await resultPromise;
  assert.equal(result.status, "timeout");
  assert.equal(h.state.devices[0].state.power, false);
  assert.equal(h.state.acceptedCommands.has("salon"), false);
});

test("a second tap while a command is pending is ignored", async () => {
  const pending = deferred();
  let calls = 0;
  const h = harness({ api: async () => { calls += 1; return pending.promise; } });
  const first = h.controller.send("salon", "power", true);
  const second = await h.controller.send("salon", "power", false);
  assert.equal(second.status, "ignored");
  assert.equal(calls, 1);
  assert.equal(h.state.devices[0].state.power, true);
  pending.resolve({ accepted: true, state: null });
  await first;
});

test("all controls use the same immediate optimistic path", async () => {
  const cases = [
    ["power", true, state => assert.equal(state.power, true)],
    ["set_mode", "heat", state => assert.equal(state.mode, "heat")],
    ["set_temperature", 26, state => assert.equal(state.target_temperature, 26)],
    ["set_fan", "high", state => assert.equal(state.fan_mode, "high")],
    ["set_vertical_swing", "swing", state => assert.equal(state.vertical_swing, "swing")],
    ["set_horizontal_swing", "swing", state => assert.equal(state.horizontal_swing, "swing")],
    ["set_advanced", true, state => assert.equal(state.advanced.quiet, true), "quiet"],
  ];
  for (const [operation, value, assertion, key] of cases) {
    const pending = deferred();
    const h = harness({ api: async () => pending.promise });
    const resultPromise = h.controller.send("salon", operation, value, key || null);
    assertion(h.state.devices[0].state);
    pending.resolve({ accepted: true, state: null });
    await resultPromise;
  }
});

test("an old SSE/state response cannot overwrite a freshly accepted optimistic value", async () => {
  let timestamp = 100;
  const pending = deferred();
  const h = harness({ now: () => timestamp, api: async () => pending.promise });
  const command = h.controller.send("salon", "power", true);
  pending.resolve({ accepted: true, state: null });
  await command;

  const stale = device({ power: false });
  h.controller.reconcileDevices([stale]);
  assert.equal(h.state.devices[0].state.power, true);
  assert.equal(h.state.acceptedCommands.has("salon"), true);

  timestamp = 5000;
  h.controller.reconcileDevices([stale]);
  assert.equal(h.state.devices[0].state.power, false);
  assert.equal(h.state.acceptedCommands.has("salon"), false);
});

test("a stale timer event cannot overwrite an optimistic timer mutation", () => {
  const optimistic = {
    id: "timer-1", device_id: "salon", action: "off", execute_at: "2026-09-05T02:00:00Z",
    status: "cancelled", command: {}, idempotency_key: "timer-1", created_at: "2026-09-05T00:00:00Z",
    updated_at: "2026-09-05T00:30:00Z", executed_at: null, error: null,
  };
  const stale = { ...optimistic, status: "scheduled", updated_at: "2026-09-05T00:29:59Z" };
  const merged = mergeTimerMutations([stale], new Map([["timer-1", { optimistic }]]));
  assert.equal(merged.length, 1);
  assert.equal(merged[0].status, "cancelled");
});
