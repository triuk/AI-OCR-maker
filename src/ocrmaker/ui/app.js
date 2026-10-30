"use strict";
// Plain JS WebUI over the loopback API; the session token comes from /bootstrap.json, never from the URL.
const $ = id => document.getElementById(id);
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const STATE_LABELS = {pending: "Čeká", running: "Probíhá", done: "Hotovo", warning: "Hotovo s varováním", failed: "Selhalo"};
const POLL_INTERVAL = 1500;
// What a stopped step may offer (its state's "action"): the button label and the API operation it posts.
const STEP_ACTIONS = {remove_text_layer: {label: "Odstranit textovou vrstvu a pokračovat", path: "text-layer/remove"}};
let token = "", projects = [], steps = [], choices = {}, currentId = null, selectedStep = null;
let refreshBusy = false, toastTimer;

// Step previews of individual steps register here: stepPreviews[stepId] = async (container, project, step) => {}.
// The container is emptied before each call; page images and overlays come from apiUrl()/api().
// A step's preview lives in ui/previews/<step id>.js and is loaded when first needed (the listing's
// `preview` flag says it exists); most use ocrmaker.pageViewer from viewer.js.
const stepPreviews = window.stepPreviews = {};
const previewScripts = {};
function loadPreview(stepId) {
  if (stepPreviews[stepId]) return Promise.resolve(stepPreviews[stepId]);
  if (!steps.some(s => s.id === stepId && s.preview)) return Promise.resolve(null);
  previewScripts[stepId] ??= new Promise(resolve => {
    const script = document.createElement("script");
    script.src = `previews/${encodeURIComponent(stepId)}.js`;
    script.onload = () => resolve(stepPreviews[stepId] || null);
    script.onerror = () => { delete previewScripts[stepId]; script.remove(); resolve(null); };
    document.head.appendChild(script);
  });
  return previewScripts[stepId];
}

function toast(message) {
  $("toast").textContent = message; $("toast").hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $("toast").hidden = true, 6500);
}
// Czech plural form for a count: one, two to four, five and more.
function plural(n, one, few, many) { return n === 1 ? one : n >= 2 && n <= 4 ? few : many; }
function seconds(value) { return value == null ? "" : value.toLocaleString("cs-CZ", {maximumFractionDigits: 1}) + " s"; }
function dateTime(value) { return value ? new Date(value).toLocaleString("cs-CZ") : ""; }

// A URL usable in <img src> and similar, where no header can carry the token.
function apiUrl(path) { return "/api" + path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token); }
async function api(path, method = "GET", body) {
  const headers = {"X-Ocrmaker-Token": token};
  if (body !== undefined && !(body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch("/api" + path, {method, headers, body: body instanceof FormData || body === undefined ? body : JSON.stringify(body)});
  if (!response.ok) {
    let error; try { error = await response.json(); } catch { error = {}; }
    const failure = new Error(typeof error.detail === "string" ? error.detail : `Operace selhala (${response.status}).`);
    failure.status = response.status; failure.data = error;
    throw failure;
  }
  return response.json();
}
async function act(fn) { try { await fn(); } catch (e) { toast(e.message); } }
window.ocrmaker = {api, apiUrl, esc, plural, toast};

function current() { return projects.find(p => p.id === currentId) || null; }
function projectStatus(p) {
  if (p.running) return {cls: "running", label: "Zpracovává se"};
  if (p.steps.some(s => s.state === "failed")) return {cls: "failed", label: "Selhalo"};
  const exported = p.steps.find(s => s.id === "export");
  if (exported && ["done", "warning"].includes(exported.state)) return {cls: "done", label: "Exportováno"};
  if (p.steps.some(s => s.state !== "pending")) return {cls: "", label: "Rozpracováno"};
  return {cls: "", label: "Nový"};
}
function finishedShare(p) {
  const implemented = p.steps.filter(s => s.implemented);
  return implemented.length ? implemented.filter(s => ["done", "warning"].includes(s.state)).length / implemented.length : 0;
}

function renderProjects() {
  const term = $("projectSearch").value.trim().toLowerCase();
  const visible = projects.filter(p => p.name.toLowerCase().includes(term));
  $("projectCount").textContent = projects.length;
  $("projects").innerHTML = visible.map(p => {
    const status = projectStatus(p);
    return `<button class="project-card ${p.id === currentId ? "selected" : ""}" data-project="${esc(p.id)}" aria-current="${p.id === currentId}">
      <span class="name">${esc(p.name)}</span>
      <span class="card-detail"><span>${esc(status.label)}</span><span>${p.source.pages} ${plural(p.source.pages, "strana", "strany", "stran")}</span></span>
      <div class="card-bar"><span style="width:${Math.round(100 * finishedShare(p))}%"></span></div></button>`;
  }).join("") || `<p class="hint" style="padding:0 7px">${projects.length ? "Žádný projekt neodpovídá hledání." : "Zatím žádné projekty."}</p>`;
  $("projects").querySelectorAll("[data-project]").forEach(card => card.onclick = () => selectProject(card.dataset.project));
}

function renderWorkspace() {
  const p = current();
  $("empty").hidden = !!p; $("workspace").hidden = !p;
  if (!p) return;
  const status = projectStatus(p);
  const language = (choices.language || []).find(c => c.id === p.settings.language);
  $("projectName").textContent = p.name;
  $("projectState").textContent = status.label;
  $("projectState").className = "badge " + status.cls;
  $("projectMeta").textContent = `${p.source.file_name} · ${p.source.pages} ${plural(p.source.pages, "strana", "strany", "stran")} · ${language ? language.label : p.settings.language}`
    + (p.working_copy ? " · starší textová vrstva odstraněna" : "");
  const anyStep = p.steps.some(s => s.implemented);
  const exportable = p.steps.some(s => s.implemented && (s.manual || s.id === "export"));
  $("runButton").disabled = p.running || !anyStep;
  $("runButton").title = anyStep ? "Spustí automatické kroky až po ruční kontrolu" : "Pracovní postup zatím neobsahuje žádný proveditelný krok";
  $("stopButton").disabled = !p.running;
  $("exportButton").disabled = p.running || !exportable;
  $("exportButton").title = exportable ? "" : "Export zatím není k dispozici";
  $("settingsButton").disabled = false;
  const failed = p.steps.find(s => s.state === "failed");
  $("projectError").hidden = !failed;
  $("projectError").textContent = failed ? `${failed.label}: ${failed.message || "krok selhal."}` : "";
  const offer = failed && STEP_ACTIONS[failed.action];
  if (offer) {
    const button = document.createElement("button");
    button.className = "primary notice-action"; button.textContent = offer.label; button.disabled = p.running;
    button.onclick = () => act(async () => { await api(`/projects/${p.id}/${offer.path}`, "POST"); await refresh(); });
    $("projectError").append(button);
  }
  renderSteps(p);
  renderStepPreview(p);
}

function renderSteps(p) {
  $("steps").innerHTML = p.steps.map((s, index) => {
    const progress = s.state === "running" && s.progress ? `<progress max="${s.progress.total || 1}" value="${s.progress.done}"></progress>` : "";
    const title = [s.implemented ? "" : "Krok zatím není k dispozici.", s.message || ""].filter(Boolean).join("\n");
    return `<li><button class="step ${s.state} ${s.implemented ? "" : "unavailable"} ${s.id === selectedStep ? "selected" : ""}" data-step="${esc(s.id)}" title="${esc(title)}">
      <span class="number">${index + 1}</span><span class="label">${esc(s.label)}</span>${progress}
      <span class="state">${esc(STATE_LABELS[s.state] || s.state)}</span></button></li>`;
  }).join("");
  $("steps").querySelectorAll("[data-step]").forEach(tile => tile.onclick = () => { selectedStep = tile.dataset.step; renderWorkspace(); });
}

let previewKey = "";
function renderStepPreview(p) {
  const container = $("stepPreview");
  const step = p.steps.find(s => s.id === selectedStep);
  if (!step) {
    previewKey = "";
    container.innerHTML = `<p class="placeholder">Klepnutím na krok pracovního postupu otevřete jeho náhled.</p>`;
    return;
  }
  // Re-render only when the step changed, so a preview being zoomed is not reset by polling.
  const key = [p.id, step.id, step.state, step.run_id, step.finished, step.progress && step.progress.done].join("|");
  if (key === previewKey) return;
  previewKey = key;
  const facts = [STATE_LABELS[step.state], step.started && `zahájeno ${dateTime(step.started)}`,
                 step.duration != null && `trvání ${seconds(step.duration)}`,
                 step.progress && `${step.progress.label || ""} ${step.progress.done}/${step.progress.total}`].filter(Boolean);
  container.innerHTML = `<h2>${esc(step.label)}</h2><div class="facts">${esc(facts.join(" · "))}</div>
    ${step.message ? `<div class="notice ${step.state === "failed" ? "error" : ""}">${esc(step.message)}</div>` : ""}<div class="preview-body"></div>`;
  const body = container.querySelector(".preview-body");
  if (!step.implemented) body.innerHTML = `<p class="placeholder">Tento krok zatím není k dispozici.</p>`;
  else if (!["done", "warning"].includes(step.state)) body.innerHTML = `<p class="placeholder">Náhled bude k dispozici po dokončení kroku.</p>`;
  else act(async () => {
    const preview = await loadPreview(step.id);
    if (key !== previewKey) return;  // another step or state was opened meanwhile
    if (!preview) body.innerHTML = `<p class="placeholder">Tento krok zatím nemá náhled.</p>`;
    else await preview(body, p, step);
  });
}

async function refresh() {
  if (refreshBusy) return;
  refreshBusy = true;
  try {
    const data = await api("/projects");
    projects = data.projects; steps = data.steps; choices = data.choices;
    if (currentId && !current()) { currentId = null; selectedStep = null; }
    $("connection").textContent = "Připojeno"; $("connection").className = "connection online";
    renderProjects(); renderWorkspace();
  } catch (e) {
    $("connection").textContent = "Aplikace neodpovídá"; $("connection").className = "connection offline";
  } finally {
    refreshBusy = false;
  }
}

function selectProject(id) {
  if (id !== currentId) { selectedStep = null; previewKey = ""; }
  currentId = id;
  try { localStorage.setItem("ocrmaker.project", id); } catch {}
  renderProjects(); renderWorkspace();
}

function fillLanguages(select, value) {
  select.innerHTML = (choices.language || []).map(c => `<option value="${esc(c.id)}">${esc(c.label)}</option>`).join("");
  select.value = value;
}

function openNewProject() {
  $("newForm").reset();
  fillLanguages($("newLanguage"), "ces");
  $("newDialog").showModal();
}

async function createProject(event) {
  event.preventDefault();
  const file = $("newSource").files[0];
  if (!file) return toast("Vyberte zdrojové PDF.");
  const form = new FormData();
  form.append("source", file);
  form.append("name", $("newName").value);
  form.append("language", $("newLanguage").value);
  $("newSubmit").disabled = true;
  try {
    const project = await api("/projects", "POST", form);
    $("newDialog").close();
    await refresh();
    selectProject(project.id);
    toast(`Projekt „${project.name}“ je vytvořený.`);
  } finally {
    $("newSubmit").disabled = false;
  }
}

// AI provider and model; an empty model value means the provider's default model.
function fillProviders(provider, model) {
  $("settingsProvider").innerHTML = (choices.ai_provider || []).map(c => `<option value="${esc(c.id)}">${esc(c.label)}</option>`).join("");
  $("settingsProvider").value = provider;
  fillModels(model);
}
function fillModels(model) {
  const provider = (choices.ai_provider || []).find(c => c.id === $("settingsProvider").value);
  const models = (choices.ai_model || {})[$("settingsProvider").value] || [];
  const fallback = provider && models.find(m => m.id === provider.default_model);
  $("settingsModel").innerHTML = `<option value="">Výchozí${fallback ? ` (${esc(fallback.label)})` : ""}</option>` +
    models.map(m => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("");
  $("settingsModel").value = model || "";
}
function aiChoice() { return {ai_provider: $("settingsProvider").value, ai_model: $("settingsModel").value || null}; }

function openSettings() {
  const p = current();
  if (!p) return;
  fillLanguages($("settingsLanguage"), p.settings.language);
  fillProviders(p.settings.ai_provider, p.settings.ai_model);
  $("checkAiResult").textContent = ""; $("checkAiResult").className = "check-result";
  $("settingsUnreadable").value = p.settings.problem_unreadable_confidence;
  $("settingsDoubtful").value = p.settings.problem_doubtful_confidence;
  $("settingsDictionary").value = (p.settings.project_dictionary || []).join("\n");
  $("settingsReconstruction").value = p.settings.reconstruction_accept_confidence;
  $("settingsDialog").showModal();
}

async function saveSettings(event) {
  event.preventDefault();
  await api(`/projects/${currentId}/settings`, "PUT", {language: $("settingsLanguage").value, ...aiChoice(),
    problem_unreadable_confidence: Number($("settingsUnreadable").value),
    problem_doubtful_confidence: Number($("settingsDoubtful").value),
    project_dictionary: $("settingsDictionary").value,
    reconstruction_accept_confidence: Number($("settingsReconstruction").value)});
  $("settingsDialog").close();
  await refresh();
}

// Sends a tiny request with the chosen (possibly unsaved) provider and model.
async function checkAi() {
  const result = $("checkAiResult");
  $("checkAi").disabled = true;
  result.className = "check-result"; result.textContent = "Ověřuji…";
  try {
    const answer = await api(`/projects/${currentId}/ai-check`, "POST", aiChoice());
    result.className = "check-result " + (answer.ok ? "ok" : "failed");
    result.textContent = answer.ok ? `${answer.message} Odpověď za ${seconds(answer.duration)}.` : answer.message;
  } catch (e) {
    result.className = "check-result failed"; result.textContent = e.message;
  } finally {
    $("checkAi").disabled = false;
  }
}

function tokens(usage) {
  if (!usage) return "spotřeba tokenů neznámá";
  const n = value => value.toLocaleString("cs-CZ");
  return `vstup ${n(usage.input)} (z mezipaměti ${n(usage.cached_input)}) · výstup ${n(usage.output)} tokenů`;
}
function recordMessage(r) {
  if (r.kind !== "ai_request") return r.message || "";
  const provider = ((choices.ai_provider || []).find(c => c.id === r.provider) || {}).label || r.provider;
  return [`${provider} · ${r.model || "výchozí model"} · ${r.purpose}`, tokens(r.usage), r.error].filter(Boolean).join(" · ");
}

async function deleteProject() {
  const p = current();
  if (!p || !confirm(`Smazat projekt „${p.name}“ se všemi výsledky? Zdrojové PDF na disku zůstane.`)) return;
  await api(`/projects/${p.id}`, "DELETE");
  $("settingsDialog").close();
  currentId = null;
  await refresh();
  toast("Projekt je smazaný.");
}

async function showDiagnostics() {
  const data = await api(`/projects/${currentId}/diagnostics`);
  const labels = Object.fromEntries(steps.map(s => [s.id, s.label]));
  const rows = data.records.filter(r => r.kind !== "step_started").reverse();
  $("diagnosticsBody").innerHTML = rows.length ? `<table><thead><tr><th>Čas</th><th>Záznam</th><th>Krok</th><th>Výsledek</th><th>Trvání</th><th>Zpráva</th></tr></thead><tbody>${
    rows.map(r => `<tr><td>${esc(dateTime(r.time))}</td><td>${esc(r.kind)}</td><td>${esc(labels[r.step] || r.step || "")}</td><td>${esc(r.outcome || "")}</td><td>${esc(seconds(r.duration))}</td><td>${esc(recordMessage(r))}</td></tr>`).join("")
  }</tbody></table>` : `<p class="hint">Projekt zatím nemá žádný záznam.</p>`;
  $("diagnosticsDialog").showModal();
}

function wireControls() {
  $("newProject").onclick = openNewProject;
  $("emptyNewProject").onclick = openNewProject;
  $("newForm").onsubmit = event => act(() => createProject(event));
  $("settingsButton").onclick = openSettings;
  $("settingsForm").onsubmit = event => act(() => saveSettings(event));
  $("deleteProject").onclick = () => act(deleteProject);
  $("settingsProvider").onchange = () => fillModels(null);
  $("checkAi").onclick = checkAi;
  $("diagnosticsButton").onclick = () => act(showDiagnostics);
  $("runButton").onclick = () => act(async () => { await api(`/projects/${currentId}/run`, "POST"); await refresh(); });
  $("exportButton").onclick = () => act(async () => { await api(`/projects/${currentId}/export`, "POST"); await refresh(); });
  $("stopButton").onclick = () => act(async () => { await api(`/projects/${currentId}/stop`, "POST"); toast("Zpracování se zastavuje…"); });
  $("projectSearch").oninput = renderProjects;
  $("shutdown").onclick = () => act(async () => {
    if (!confirm("Vypnout aplikaci? Probíhající zpracování se zastaví, hotové kroky zůstanou uložené.")) return;
    await api("/application/shutdown", "POST");
    $("connection").textContent = "Aplikace je vypnutá"; $("connection").className = "connection offline";
  });
  document.querySelectorAll("[data-close]").forEach(button => button.onclick = () => button.closest("dialog").close());
}

async function bootstrap() {
  wireControls();
  try {
    token = (await (await fetch("/bootstrap.json")).json()).token;
  } catch {
    $("connection").textContent = "Aplikace neodpovídá"; $("connection").className = "connection offline";
    return;
  }
  await refresh();
  let remembered = null;
  try { remembered = localStorage.getItem("ocrmaker.project"); } catch {}
  const initial = projects.find(p => p.id === remembered) || projects[0];
  if (initial) selectProject(initial.id);
  setInterval(refresh, POLL_INTERVAL);
}
document.addEventListener("DOMContentLoaded", bootstrap);
