const state = { token: sessionStorage.getItem("haierToken") || localStorage.getItem("haierToken") || "", devices: [], timers: [], eventAbort: null, setupFlow: null };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const modeMeta = {
  auto: ["Auto", "◇"], cool: ["Frío", "❄"], heat: ["Calor", "☀"],
  dry: ["Seco", "◌"], fan: ["Ventilador", "✣"], off: ["Apagado", "○"]
};
const labels = { auto:"Auto", cool:"Frío", heat:"Calor", dry:"Seco", fan:"Ventilador", low:"Bajo", medium:"Medio", high:"Alto", swing:"Oscilar", fixed:"Fijo" };

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) { openTokenDialog("El token no es válido o ha sido revocado."); throw new Error("unauthorized"); }
  let body = null;
  if (response.headers.get("content-type")?.includes("json")) body = await response.json();
  if (!response.ok) throw new Error(body?.detail || `Error ${response.status}`);
  return body;
}

function escapeHtml(value) { const element = document.createElement("span"); element.textContent = String(value ?? ""); return element.innerHTML; }
function showError(message) { const banner = $("#errorBanner"); banner.textContent = message; banner.classList.toggle("hidden", !message); }
function toast(message) { const node = $("#toast"); node.textContent = message; node.classList.add("show"); clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.remove("show"), 2600); }
function localTime(value) { return new Intl.DateTimeFormat("es-ES", { hour:"2-digit", minute:"2-digit" }).format(new Date(value)); }
function countdown(value) {
  const seconds = Math.max(0, Math.floor((new Date(value).getTime() - Date.now()) / 1000));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60); if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60); const rest = minutes % 60;
  return `${hours} h ${rest ? `${rest} min` : ""}`.trim();
}

function nextTimer(deviceId) { return state.timers.filter(t => t.device_id === deviceId && t.status === "scheduled").sort((a,b) => new Date(a.execute_at)-new Date(b.execute_at))[0]; }
function cardMode(device) { return device.state.power ? (device.state.mode || "auto") : "off"; }

function render() {
  const root = $("#devices");
  if (!state.devices.length) { root.innerHTML = `<div class="banner">No hay aires anunciados por el adaptador actual.</div>`; return; }
  root.innerHTML = state.devices.map(deviceCard).join("");
  bindCards();
}

function deviceCard(device) {
  const mode = cardMode(device); const [modeLabel, glyph] = modeMeta[mode] || [mode, "·"];
  const caps = device.capabilities; const timer = nextTimer(device.id);
  const roomTemp = device.state.room_temperature == null ? "Sin dato de ambiente" : `Habitación ${Number(device.state.room_temperature).toFixed(1).replace(".0","")}°`;
  const target = device.state.target_temperature == null ? "—" : Number(device.state.target_temperature).toFixed(1).replace(".0","");
  const modeChips = caps.modes.map(item => `<button class="chip ${device.state.mode === item ? "active" : ""}" data-command="set_mode" data-value="${item}">${escapeHtml(labels[item] || item)}</button>`).join("");
  const fanChips = caps.fan_modes.map(item => `<button class="chip ${device.state.fan_mode === item ? "active" : ""}" data-command="set_fan" data-value="${item}">${escapeHtml(labels[item] || item)}</button>`).join("");
  const timerHtml = timer ? `<span><strong>${timer.action === "on" ? "Encender" : "Apagar"} en <span data-countdown="${timer.execute_at}">${countdown(timer.execute_at)}</span></strong><small>${localTime(timer.execute_at)} · toca para editar</small></span><span class="timer-pulse"></span>` : `<span><strong>Temporizador</strong><small>Encender o apagar después</small></span><span>＋</span>`;
  return `<article class="device-card ${mode}" data-device="${device.id}">
    <div class="card-content">
      <div class="card-head"><div><div class="room-name">${escapeHtml(device.name)}</div><div class="mode-caption"><span class="mode-glyph">${glyph}</span><span>${modeLabel}${device.state.stale ? " · dato antiguo" : ""}</span></div></div>
      <button class="power" type="button" data-power aria-pressed="${Boolean(device.state.power)}" aria-label="${device.state.power ? "Apagar" : "Encender"} ${escapeHtml(device.name)}">⌁</button></div>
      <div class="temperature-block"><div><div class="target-temp">${target}<sup>°</sup></div><div class="room-temp">${roomTemp}</div></div></div>
      ${caps.temperature_min != null ? `<div class="temp-controls"><button data-temp="down" aria-label="Bajar temperatura">−</button><button data-temp="up" aria-label="Subir temperatura">＋</button></div>` : ""}
      ${modeChips ? `<div class="control-section"><div class="control-label"><span>Modo</span><span>${modeLabel}</span></div><div class="chips">${modeChips}</div></div>` : ""}
      ${fanChips ? `<div class="control-section"><div class="control-label"><span>Ventilador</span><span>${escapeHtml(labels[device.state.fan_mode] || device.state.fan_mode || "—")}</span></div><div class="chips">${fanChips}</div></div>` : ""}
      <div class="actions"><button class="control-button timer-summary" data-timer>${timerHtml}</button><button class="control-button" data-more aria-label="Más controles">•••</button></div>
    </div></article>`;
}

function bindCards() {
  $$(".device-card").forEach(card => {
    const device = state.devices.find(item => item.id === card.dataset.device);
    $("[data-power]", card)?.addEventListener("click", () => send(device.id, "power", !device.state.power));
    $$('[data-command]', card).forEach(button => button.addEventListener("click", () => send(device.id, button.dataset.command, button.dataset.value)));
    $$('[data-temp]', card).forEach(button => button.addEventListener("click", () => {
      const step = device.capabilities.temperature_step || 1; const current = device.state.target_temperature;
      if (current == null) return;
      const value = button.dataset.temp === "up" ? current + step : current - step;
      if (value >= device.capabilities.temperature_min && value <= device.capabilities.temperature_max) send(device.id, "set_temperature", value);
    }));
    $("[data-timer]", card).addEventListener("click", () => openTimer(device));
    $("[data-more]", card).addEventListener("click", () => openMore(device));
  });
}

async function send(deviceId, operation, value, key = null) {
  try {
    const result = await api(`/api/v1/devices/${deviceId}/commands`, { method:"POST", body:JSON.stringify({ operation, value, key }) });
    if (!result.accepted) { toast(result.message); return; }
    await refresh(); toast("Cambio confirmado");
  } catch (error) { showError(error.message); }
}

function openTimer(device) {
  const dialog = $("#timerDialog"), timer = nextTimer(device.id);
  $("#timerDeviceId").value = device.id; $("#timerEditId").value = timer?.id || "";
  $("#timerTitle").textContent = timer ? `Editar · ${device.name}` : `Programar · ${device.name}`;
  if (timer) {
    $(`input[name=timerAction][value=${timer.action}]`).checked = true;
    $("input[name=timerKind][value=exact]").checked = true;
    $("#timerExact").value = new Date(new Date(timer.execute_at).getTime() - new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
  } else { $("input[name=timerKind][value=relative]").checked = true; $("#timerMinutes").value = 30; }
  const caps = device.capabilities;
  $("#timerMode").innerHTML = caps.modes.map(item => `<option value="${item}">${labels[item] || item}</option>`).join("");
  $("#timerMode").value = timer?.command?.mode || device.state.mode || caps.modes[0] || "auto";
  $("#timerTemperature").min = caps.temperature_min ?? ""; $("#timerTemperature").max = caps.temperature_max ?? ""; $("#timerTemperature").step = caps.temperature_step ?? 1;
  $("#timerTemperature").value = timer?.command?.temperature ?? device.state.target_temperature ?? caps.temperature_min ?? 22;
  $("#timerFan").innerHTML = `<option value="">Sin cambiar</option>` + caps.fan_modes.map(item => `<option value="${item}">${labels[item] || item}</option>`).join("");
  $("#timerFan").value = timer?.command?.fan_mode || "";
  syncTimerFields(); dialog.showModal();
}

function syncTimerFields() {
  const exact = $("input[name=timerKind]:checked").value === "exact";
  $("#relativeFields").classList.toggle("hidden", exact); $("#exactFields").classList.toggle("hidden", !exact);
  const on = $("input[name=timerAction]:checked").value === "on"; $("#onOptions").classList.toggle("hidden", !on);
}

async function saveTimer(event) {
  event.preventDefault(); const deviceId = $("#timerDeviceId").value, editId = $("#timerEditId").value;
  const action = $("input[name=timerAction]:checked").value, exact = $("input[name=timerKind]:checked").value === "exact";
  const executeAt = exact ? new Date($("#timerExact").value) : new Date(Date.now() + Number($("#timerMinutes").value)*60000);
  if (Number.isNaN(executeAt.getTime()) || executeAt <= new Date()) { $("#timerError").textContent = "Elige una hora futura."; return; }
  const command = action === "on" ? { mode:$("#timerMode").value, temperature:Number($("#timerTemperature").value), ...($("#timerFan").value ? {fan_mode:$("#timerFan").value} : {}) } : {};
  try {
    if (editId) await api(`/api/v1/timers/${editId}`, { method:"PATCH", body:JSON.stringify({ execute_at:executeAt.toISOString(), command }) });
    else await api("/api/v1/timers", { method:"POST", body:JSON.stringify({ device_id:deviceId, action, execute_at:executeAt.toISOString(), command }) });
    $("#timerDialog").close(); await refresh(); toast(editId ? "Temporizador actualizado" : "Temporizador programado");
  } catch (error) { $("#timerError").textContent = error.message; }
}

function openMore(device) {
  $("#moreTitle").textContent = `Más · ${device.name}`; const root = $("#advancedControls");
  const rows = [];
  if (device.capabilities.vertical_swing.length) rows.push(selectRow("Swing vertical", "set_vertical_swing", device.capabilities.vertical_swing, device.state.vertical_swing));
  if (device.capabilities.horizontal_swing.length) rows.push(selectRow("Swing horizontal", "set_horizontal_swing", device.capabilities.horizontal_swing, device.state.horizontal_swing));
  device.capabilities.advanced.forEach(item => {
    if (item.kind === "toggle") rows.push(`<div class="advanced-row"><span>${escapeHtml(item.label)}</span><button class="switch" type="button" aria-pressed="${Boolean(device.state.advanced[item.key])}" data-advanced="${item.key}" aria-label="${escapeHtml(item.label)}"></button></div>`);
  });
  root.innerHTML = rows.join("") || `<p class="muted">Este modelo no anuncia controles avanzados.</p>`;
  $$('[data-advanced]', root).forEach(button => button.addEventListener("click", async () => { await send(device.id, "set_advanced", button.getAttribute("aria-pressed") !== "true", button.dataset.advanced); $("#moreDialog").close(); }));
  $$('[data-more-command]', root).forEach(select => select.addEventListener("change", async () => { await send(device.id, select.dataset.moreCommand, select.value); $("#moreDialog").close(); }));
  $("#moreDialog").showModal();
}
function selectRow(label, command, options, selected) { return `<div class="advanced-row"><label>${label}<select data-more-command="${command}">${options.map(value => `<option value="${value}" ${value === selected ? "selected" : ""}>${escapeHtml(labels[value] || value.replace("position_","Posición "))}</option>`).join("")}</select></label></div>`; }

function openTokenDialog(message = "") { $("#tokenError").textContent = message; if (!$("#tokenDialog").open) $("#tokenDialog").showModal(); }
async function saveToken(event) {
  event.preventDefault(); const token = $("#apiToken").value.trim(); state.token = token;
  try {
    await api("/healthz"); await api("/api/v1/devices");
    const store = $("#rememberToken").checked ? localStorage : sessionStorage; store.setItem("haierToken", token);
    ($("#rememberToken").checked ? sessionStorage : localStorage).removeItem("haierToken");
    $("#tokenDialog").close(); await refresh(); connectEvents();
  } catch (error) { if (error.message !== "unauthorized") $("#tokenError").textContent = error.message; }
}

async function saveHaierSetup(event) {
  event.preventDefault(); const errorNode = $("#haierSetupError"); errorNode.textContent = "";
  try {
    let result;
    if (!state.setupFlow) {
      result = await api("/api/v1/setup/haier/start", { method:"POST", body:JSON.stringify({ pairing_token:$("#pairingToken").value, email:$("#haierEmail").value, password:$("#haierPassword").value }) });
      $("#haierPassword").value = "";
      if (result.status === "mfa_required") {
        state.setupFlow = { id:result.flow_id, csrf:result.csrf_token };
        $("#credentialsStep").classList.add("hidden"); $("#otpStep").classList.remove("hidden");
        $("#setupEyebrow").textContent = "DOBLE FACTOR"; $("#setupTitle").textContent = "Revisa tu correo"; $("#haierSetupSubmit").textContent = "Verificar y conectar"; $("#haierOtp").focus(); return;
      }
    } else {
      result = await api("/api/v1/setup/haier/otp", { method:"POST", body:JSON.stringify({ flow_id:state.setupFlow.id, csrf_token:state.setupFlow.csrf, code:$("#haierOtp").value }) });
      $("#haierOtp").value = "";
    }
    if (result.status === "complete") {
      state.setupFlow = null;
      if (result.api_token) { state.token = result.api_token; sessionStorage.setItem("haierToken", result.api_token); }
      $("#haierSetupDialog").close(); toast("hOn conectado");
      if (state.token) { await refresh(); connectEvents(); } else openTokenDialog("Conexión hOn completada; introduce tu token local.");
    }
  } catch (error) { $("#haierPassword").value = ""; errorNode.textContent = error.message; }
}

async function resendOtp() {
  if (!state.setupFlow) return;
  try { await api("/api/v1/setup/haier/resend", { method:"POST", body:JSON.stringify({ flow_id:state.setupFlow.id, csrf_token:state.setupFlow.csrf }) }); toast("Código reenviado"); }
  catch (error) { $("#haierSetupError").textContent = error.message; }
}

async function refresh() {
  if (!state.token) { openTokenDialog(); return; }
  try {
    const [devices, timers] = await Promise.all([api("/api/v1/devices"), api("/api/v1/timers?include_finished=true")]);
    state.devices = devices; state.timers = timers; showError(""); render(); setConnection(true, "En línea");
  } catch (error) { if (error.message !== "unauthorized") { showError(error.message); setConnection(false, "Degradado"); } }
}
function setConnection(ok, label) { const button=$("#connectionButton"); button.classList.toggle("online",ok); button.classList.toggle("error",!ok); $("#connectionLabel").textContent=label; }

async function connectEvents() {
  state.eventAbort?.abort(); const controller = new AbortController(); state.eventAbort = controller;
  try {
    const response = await fetch("/api/v1/events", { headers:{Authorization:`Bearer ${state.token}`}, signal:controller.signal });
    if (!response.ok || !response.body) return;
    const reader=response.body.getReader(), decoder=new TextDecoder(); let buffer="";
    while (true) { const {done,value}=await reader.read(); if(done) break; buffer+=decoder.decode(value,{stream:true}); const blocks=buffer.split("\n\n"); buffer=blocks.pop(); if(blocks.some(block=>block.startsWith("event: device")||block.startsWith("event: timer"))) await refresh(); }
  } catch (error) { if (error.name !== "AbortError") setTimeout(connectEvents, 5000); }
}

$("#tokenForm").addEventListener("submit", saveToken); $("#timerForm").addEventListener("submit", saveTimer); $("#haierSetupForm").addEventListener("submit", saveHaierSetup); $("#resendOtp").addEventListener("click", resendOtp);
$$('input[name=timerKind], input[name=timerAction]').forEach(input => input.addEventListener("change", syncTimerFields));
$$('[data-minutes]').forEach(button => button.addEventListener("click", () => $("#timerMinutes").value = button.dataset.minutes));
$$('[data-close]').forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
$("#connectionButton").addEventListener("click", refresh);
setInterval(() => { $$('[data-countdown]').forEach(node => node.textContent = countdown(node.dataset.countdown)); }, 1000);
async function boot() {
  try {
    const setup = await fetch("/api/v1/setup/haier/status", { cache:"no-store" }).then(response => response.json());
    if (setup.status === "complete" && setup.api_token) {
      state.token = setup.api_token; sessionStorage.setItem("haierToken", setup.api_token);
    }
    if (setup.status === "mfa_required") {
      state.setupFlow = { id:setup.flow_id, csrf:setup.csrf_token };
      $("#credentialsStep").classList.add("hidden"); $("#otpStep").classList.remove("hidden");
      $("#setupEyebrow").textContent = "DOBLE FACTOR"; $("#setupTitle").textContent = "Revisa tu correo";
      $("#haierSetupSubmit").textContent = "Verificar y conectar"; $("#haierSetupDialog").showModal(); return;
    }
    const health = await fetch("/healthz", { cache:"no-store" }).then(response => response.json());
    if (health.setup_required) {
      $("#setupEyebrow").textContent = state.token ? "RECONECTAR" : "CONFIGURACIÓN INICIAL"; $("#setupTitle").textContent = state.token ? "Reconectar con hOn" : "Conectar con hOn";
      if (setup.status === "failed" && setup.message) $("#haierSetupError").textContent = setup.message;
      $("#haierSetupDialog").showModal(); return;
    }
  } catch (_) { setConnection(false, "Sin conexión"); }
  if (state.token) { refresh(); connectEvents(); } else openTokenDialog();
}
boot();
