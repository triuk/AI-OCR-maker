"use strict";
// Manual review: the scan with a switchable overlay (layout, reading order, confidence) on one side; the
// queue of illegible markers and the page's text on the other. Any word can be clicked, on the scan or in
// the text, and corrected. A manual correction is saved at once and applied at export; it never runs a step
// (ADR 0004). The queue: type the text and press Ctrl+Enter to save it and move on to the next marker.
(function () {
  const {pageViewer, box, api, apiUrl, esc, plural, toast} = window.ocrmaker;
  const MODES = {layout: "Rozvržení", order: "Pořadí čtení", confidence: "Důvěra"};
  // Which step's overlay each mode draws.
  const MODE_STEPS = {layout: "layout", order: "order", confidence: "ocr"};
  const ORIGINS = {ocr: "lokální OCR", ai: "AI rekonstrukce", publisher: "chyba v tisku (ponecháno)",
                   web: "dohledáno na webu", marker: "[NEČITELNÉ]", manual: "ruční oprava"};
  const BANDS = [[90, "ocr-high"], [75, "ocr-good"], [50, "ocr-low"], [0, "ocr-poor"]];
  const LEGENDS = {
    layout: [{label: "text", cls: "layout-body"}, {label: "nadpis", cls: "layout-heading"},
             {label: "popisek", cls: "layout-caption"}, {label: "záhlaví a zápatí", cls: "layout-header"}],
    order: [{label: "článek (barva = pořadí)", cls: "order-1"}, {label: "záhlaví", cls: "order-header"},
            {label: "zápatí", cls: "order-footer"}, {label: "inzerát", cls: "order-ad"}],
    confidence: [{label: "důvěra 90–100 %", cls: "ocr-high"}, {label: "75–90 %", cls: "ocr-good"},
                 {label: "50–75 %", cls: "ocr-low"}, {label: "pod 50 %", cls: "ocr-poor"}],
  };
  const WORD_LEGEND = [{label: "[NEČITELNÉ]", cls: "review-marker"}, {label: "ruční oprava", cls: "review-manual"}];
  const percent = value => (value == null ? "–" : `${Math.round(value)} %`);
  const quoted = text => (text ? `„${text}“` : "(prázdný rámeček)");
  let mode = "layout";  // kept while the app is open

  window.stepPreviews.review = async (container, project) => {
    const base = `/projects/${project.id}`;
    const state = {queue: {items: [], counts: {}}, corrections: [], position: 0, pages: {}, modes: {},
                   selected: null, tab: "queue", viewer: null};
    container.innerHTML = `<div class="review">
      <div class="review-scan">
        <div class="review-modes" role="group" aria-label="Překryv skenu">${Object.entries(MODES).map(([id, label]) =>
          `<button type="button" data-mode="${id}">${esc(label)}</button>`).join("")}
          <span class="review-legend"></span></div>
        <div class="review-viewer"></div>
      </div>
      <div class="review-side">
        <div class="review-summary"></div>
        <div class="review-tabs" role="tablist">
          <button type="button" role="tab" data-tab="queue">Fronta</button>
          <button type="button" role="tab" data-tab="text">Text stránky</button>
        </div>
        <div class="review-panel" data-panel="queue"></div>
        <div class="review-panel" data-panel="text" hidden></div>
      </div>
    </div>`;
    const $ = selector => container.querySelector(selector);

    // Data -----------------------------------------------------------------------------------------
    async function loadQueue() {
      [state.queue, state.corrections] = await Promise.all([
        api(`${base}/review/queue`), api(`${base}/corrections`).then(data => data.corrections)]);
      state.position = Math.min(state.position, Math.max(state.queue.items.length - 1, 0));
    }
    async function pageView(index) {
      state.pages[index] ??= api(`${base}/review/pages/${index}`);
      return state.pages[index];
    }
    async function modeOverlay(index) {
      const key = `${mode}|${index}`;
      state.modes[key] ??= api(`${base}/steps/${MODE_STEPS[mode]}/overlay/${index}`).catch(error => {
        if (error.status === 404) return null;
        throw error;
      });
      return state.modes[key];
    }
    const pageWords = view => view.articles.flatMap(article => article.blocks.flatMap(block => block.lines.flat()));
    const correctionOf = (page, target) => state.corrections.find(item => item.kind === "text" && item.page === page
                                                                    && item.ids.includes(target)) || null;
    const current = () => state.queue.items[state.position] || null;

    // Saving ---------------------------------------------------------------------------------------
    async function save(page, word, text, source) {
      const answer = await api(`${base}/corrections`, "PUT", {page, word, text, source});
      await afterChange(page);
      return answer.correction;
    }
    async function revert(page, correction) {
      await api(`${base}/corrections/${encodeURIComponent(correction.id)}`, "DELETE");
      await afterChange(page);
    }
    async function afterChange(page) {
      delete state.pages[page];
      await loadQueue();
      renderSummary();
      if (state.viewer) await state.viewer.redraw();
    }

    // Scan -----------------------------------------------------------------------------------------
    function drawMode(layer, overlay) {
      if (!overlay) return;
      if (mode === "layout") {
        for (const block of overlay.blocks || []) box(block.bbox, {class: `layout-block layout-${block.kind}`}, layer);
      } else if (mode === "order") {
        for (const item of overlay.articles || []) {
          const cls = item.kind === "article" ? `order-${(item.number - 1) % 8 + 1}` : `order-${item.kind}`;
          for (const block of item.blocks) box(block.bbox, {class: `order-block ${cls}`}, layer);
        }
      } else {
        for (const word of overlay.words || []) {
          if (word.text) box(word.bbox, {class: `ocr-word ${BANDS.find(([lowest]) => word.confidence >= lowest)[1]}`}, layer);
        }
      }
    }
    function draw(layer, overlay, page) {
      drawMode(layer, overlay.mode);
      for (const word of pageWords(overlay.view)) {
        const cls = ["review-word", word.origin === "marker" ? "review-marker" : "", word.origin === "manual" ? "review-manual" : "",
                     state.selected && state.selected.page === page.index && state.selected.target === word.target ? "review-selected" : ""];
        box(word.bbox, {class: cls.join(" "), "data-select": word.target,
                        "data-tip": `${quoted(word.text)}\n${ORIGINS[word.origin] || word.origin} · důvěra ${percent(word.confidence)}\nKlepnutím opravíte`}, layer);
      }
      const item = current();
      if (item && item.page === page.index && state.tab === "queue") box(item.bbox, {class: "review-current"}, layer);
    }
    async function load(index) {
      const [view, overlay] = await Promise.all([pageView(index), modeOverlay(index)]);
      if (state.tab === "text") renderText(view);
      return {view, mode: overlay};
    }
    function select(element, page) {
      openWord(page.index, element.getAttribute("data-select"));
    }
    function renderLegend() {
      $(".review-legend").innerHTML = [...LEGENDS[mode], ...WORD_LEGEND].map(item =>
        `<span class="legend-item"><i class="${esc(item.cls)}"></i>${esc(item.label)}</span>`).join("");
      container.querySelectorAll("[data-mode]").forEach(button => button.classList.toggle("selected", button.dataset.mode === mode));
    }

    // Side panel -----------------------------------------------------------------------------------
    function renderSummary() {
      const {markers = 0, remaining = 0} = state.queue.counts;
      const count = state.corrections.length;
      $(".review-summary").innerHTML = `<strong>${markers ? `Neopravené značky [NEČITELNÉ]: ${remaining} / ${markers}` : "Žádná značka [NEČITELNÉ]"}</strong> · ${count} ${plural(count, "ruční oprava", "ruční opravy", "ručních oprav")}
        <div class="hint">Opravy se ukládají hned a použijí se při exportu; žádný krok se kvůli nim znovu nespouští. Export je možný i se zbývajícími značkami.</div>`;
      $('[data-tab="queue"]').textContent = markers ? `Fronta (${remaining})` : "Fronta";
    }
    function showTab(tab) {
      state.tab = tab;
      container.querySelectorAll("[data-tab]").forEach(button => button.setAttribute("aria-selected", String(button.dataset.tab === tab)));
      container.querySelectorAll("[data-panel]").forEach(panel => panel.hidden = panel.dataset.panel !== tab);
      if (tab === "queue") renderQueue();
      if (state.viewer) state.viewer.redraw();
    }

    function renderQueue() {
      const panel = $('[data-panel="queue"]');
      const items = state.queue.items;
      if (!items.length) {
        panel.innerHTML = `<p class="placeholder">Automatické zpracování nenechalo žádné nečitelné místo.<br>
          Klepnutím na slovo ve skenu nebo v textu stránky můžete opravit kterékoli slovo.</p>`;
        return;
      }
      const item = current();
      const correction = item.correction;
      const value = correction ? correction.text : item.candidate || item.hint;
      panel.innerHTML = `<div class="queue-nav">${items.map((entry, index) =>
          `<button type="button" class="queue-chip ${entry.correction ? "corrected" : ""} ${index === state.position ? "selected" : ""}"
             data-item="${index}" title="Strana ${entry.page + 1}: ${esc(entry.correction ? entry.text : quoted(entry.hint))}">${index + 1}</button>`).join("")}</div>
        <div class="queue-item">
          <div class="facts">Značka ${state.position + 1} z ${items.length} · strana ${item.page + 1}${correction ? " · opraveno" : ""}</div>
          <img class="queue-crop" alt="Výřez skenu s vyznačeným místem" src="${esc(apiUrl(`${base}/review/pages/${item.page}/crop/${encodeURIComponent(item.id)}`))}">
          <p class="queue-context">${item.context.before ? `…${esc(item.context.before)} ` : ""}<mark>${esc(item.text || "∅")}</mark>${item.context.after ? ` ${esc(item.context.after)}…` : ""}</p>
          <dl class="queue-facts">
            <dt>Částečné čtení</dt><dd>${esc(quoted(item.hint))}</dd>
            <dt>Návrh AI</dt><dd>${item.candidate ? `${esc(quoted(item.candidate))} · důvěra ${percent(item.confidence)}
              <button type="button" class="link" data-use="candidate">Použít</button>` : "bez návrhu"}</dd>
          </dl>
          <label>Správný text<input class="queue-input" value="${esc(value)}" spellcheck="false" autocomplete="off"></label>
          <div class="queue-actions">
            <button type="button" data-step="-1" ${state.position === 0 ? "disabled" : ""}>‹ Předchozí</button>
            <button type="button" data-step="1" ${state.position >= items.length - 1 ? "disabled" : ""}>Další ›</button>
            ${correction ? `<button type="button" data-revert>Vrátit opravu</button>` : ""}
            <button type="button" class="primary push-left" data-save title="Ctrl+Enter">Uložit a další <kbd>Ctrl+Enter</kbd></button>
          </div>
        </div>`;
      const input = panel.querySelector(".queue-input");
      async function saveAndNext() {
        const text = input.value.trim();
        if (!text) return toast("Zadejte správný text, nebo pokračujte na další místo.");
        try {
          await save(item.page, item.id, text, "queue");
        } catch (error) { return toast(error.message); }
        const next = state.queue.items.findIndex((entry, index) => index > state.position && !entry.correction);
        if (next >= 0) state.position = next;
        else if (state.position < state.queue.items.length - 1) state.position += 1;
        else toast(state.queue.counts.remaining ? "Konec fronty; některé značky ještě zbývají." : "Všechny značky jsou opravené.");
        await goTo(state.position);
      }
      input.addEventListener("keydown", event => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); saveAndNext(); }
      });
      panel.querySelector("[data-save]").onclick = saveAndNext;
      panel.querySelectorAll("[data-step]").forEach(button => button.onclick = () => goTo(state.position + Number(button.dataset.step)));
      panel.querySelectorAll("[data-item]").forEach(button => button.onclick = () => goTo(Number(button.dataset.item)));
      const use = panel.querySelector('[data-use="candidate"]');
      if (use) use.onclick = () => { input.value = item.candidate; input.focus(); };
      const undo = panel.querySelector("[data-revert]");
      if (undo) undo.onclick = async () => {
        try { await revert(item.page, correction); await goTo(state.position); } catch (error) { toast(error.message); }
      };
      input.focus(); input.select();
    }
    async function goTo(position) {
      state.position = Math.min(Math.max(position, 0), Math.max(state.queue.items.length - 1, 0));
      renderQueue();
      const item = current();
      if (!item || !state.viewer) return;
      if (state.viewer.page() !== item.page) await state.viewer.show(item.page);
      else await state.viewer.redraw();
    }

    function renderText(view) {
      const panel = $('[data-panel="text"]');
      const selected = state.selected && state.selected.page === view.index ? state.selected : null;
      const words = pageWords(view);
      const group = selected ? words.filter(word => word.target === selected.target) : [];
      const correction = selected && correctionOf(view.index, selected.target);
      const editor = selected ? `<div class="word-editor">
          <div class="facts">Oprava slova · ${esc(ORIGINS[group[0] ? group[0].origin : "ocr"] || "")}${correction ? ` · původně ${esc(quoted(correction.previous))}` : ""}</div>
          <input class="word-input" value="${esc(group.map(word => word.text).filter(Boolean).join(" "))}" spellcheck="false" autocomplete="off">
          <div class="queue-actions">
            <button type="button" data-cancel>Zrušit</button>
            ${correction ? `<button type="button" data-revert>Vrátit opravu</button>` : ""}
            <button type="button" class="primary push-left" data-save>Uložit <kbd>Ctrl+Enter</kbd></button>
          </div></div>` : `<p class="hint">Klepněte na slovo, které chcete opravit.</p>`;
      const text = view.articles.map(article => `<article class="review-article">${article.blocks.map(block =>
        `<p class="review-block review-${esc(block.kind)}">${block.lines.map(line => line.map(word =>
          `<span class="rw rw-${esc(word.origin)} ${selected && word.target === selected.target ? "selected" : ""}" data-target="${esc(word.target)}"
             title="${esc(ORIGINS[word.origin] || word.origin)} · důvěra ${percent(word.confidence)}">${esc(word.text || "□")}</span>`).join(" ")).join(" ")}</p>`).join("")}</article>`).join("");
      panel.innerHTML = `${editor}<div class="review-text">${text || `<p class="placeholder">Stránka nemá žádný text.</p>`}</div>`;
      panel.querySelectorAll("[data-target]").forEach(span => span.onclick = () => openWord(view.index, span.dataset.target));
      if (!selected) return;
      const input = panel.querySelector(".word-input");
      async function store() {
        try {
          await save(view.index, selected.target, input.value, "word");
          state.selected = null;
        } catch (error) { toast(error.message); }
      }
      input.addEventListener("keydown", event => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); store(); }
        if (event.key === "Escape") { state.selected = null; state.viewer.redraw(); }
      });
      panel.querySelector("[data-save]").onclick = store;
      panel.querySelector("[data-cancel]").onclick = () => { state.selected = null; state.viewer.redraw(); };
      const undo = panel.querySelector("[data-revert]");
      if (undo) undo.onclick = async () => {
        try { await revert(view.index, correction); state.selected = null; await state.viewer.redraw(); } catch (error) { toast(error.message); }
      };
      input.focus(); input.select();
    }
    function openWord(page, target) {
      state.selected = {page, target};
      if (state.tab !== "text") showTab("text");
      else state.viewer.redraw();
    }

    // Start ----------------------------------------------------------------------------------------
    try {
      await loadQueue();
    } catch (error) {
      container.innerHTML = `<p class="placeholder">${esc(error.message)}</p>`;
      return;
    }
    renderSummary();
    renderLegend();
    container.querySelectorAll("[data-tab]").forEach(button => button.onclick = () => showTab(button.dataset.tab));
    container.querySelectorAll("[data-mode]").forEach(button => button.onclick = () => {
      mode = button.dataset.mode;
      renderLegend();
      state.viewer.redraw();
    });
    state.viewer = await pageViewer($(".review-viewer"), project, {id: "review"}, {draw, load, select});
    if (!state.viewer) return;  // no pages yet: the viewer said so
    showTab(state.queue.items.length ? "queue" : "text");
    if (current() && state.viewer.page() !== current().page) await state.viewer.show(current().page);
  };
})();
