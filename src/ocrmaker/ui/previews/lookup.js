"use strict";
// Web lookup step preview: the words a web source settled and the spots left unresolved (illegible markers,
// doubtful readings kept as read), with the queries, the answer and the source URLs on hover, plus a list of
// the page's web-settled words with clickable sources.
(function () {
  const {pageViewer, box, esc} = window.ocrmaker;
  const STATUSES = {
    web: {cls: "lookup-web", label: "dohledáno na webu"},
    marker: {cls: "rec-unresolved", get label() { return window.ocrmaker.marker; }},
    doubtful: {cls: "rec-doubtful", label: "pochybné čtení (ponecháno)"},
  };
  // An unresolved word shows the illegible marker, or a doubtful reading kept as read.
  const statusOf = word => word.status !== "unresolved" ? word.status
    : word.text === window.ocrmaker.marker ? "marker" : "doubtful";
  const OUTCOMES = {
    settled: "dohledáno",
    insufficient: "web nedal dost jistý doklad",
    not_found: "na webu nenalezeno",
    invalid: "výsledek byl neplatný",
    no_query: "nebylo z čeho sestavit dotaz",
    unavailable: "AI nebyla k dispozici",
    rejected: "odpověď AI byla odmítnuta",
    disabled: "dohledání na webu je vypnuto",
  };
  const percent = value => (value == null ? "–" : `${Math.round(value)} %`);
  const quoted = text => (text ? `„${text}“` : "(bez textu)");

  function describe(overlay, page) {
    if (!overlay) return `Strana ${page.index + 1}: bez výsledku dohledání`;
    const {spots, searched, settled, markers, kept = 0} = overlay.counts;
    const left = `${markers} značek ${window.ocrmaker.marker}` + (kept ? `, ${kept} pochybných čtení` : "");
    if (!overlay.enabled) return `Strana ${page.index + 1}: dohledání na webu je v nastavení vypnuto`
      + (spots ? `, zůstává ${left}` : "");
    if (!spots) return `Strana ${page.index + 1}: žádné nevyřešené místo`;
    return `Strana ${page.index + 1}: ${spots} nevyřešených míst, ${searched} hledáno, ${settled} dohledáno, `
      + `zůstává ${left}` + (overlay.rejected ? " · odpověď AI odmítnuta" : "");
  }

  function list(host, overlay) {
    const settled = (overlay ? overlay.words : []).filter(word => word.status === "web");
    host.innerHTML = settled.length ? `<h3>Slova dohledaná na webu</h3><ul>${settled.map(word => `<li>
        <b>${esc(word.text)}</b> <span class="muted">(místní čtení ${esc(quoted(word.hint))}, důvěra ${esc(percent(word.confidence))})</span>
        ${word.answer ? `<div>${esc(word.answer)}</div>` : ""}
        <div class="muted">Dotazy: ${word.queries.map(query => esc(quoted(query))).join(", ")}</div>
        <div>${word.sources.map(url => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`).join("<br>")}</div>
      </li>`).join("")}</ul>` : "";
  }

  function draw(layer, overlay, host) {
    for (const word of overlay.words) {
      const status = STATUSES[statusOf(word)];
      const lines = [`${status.label}: ${quoted(word.text)}`, `Místní čtení: ${quoted(word.hint)}`];
      if (word.status === "web") lines.push(`Důvěra: ${percent(word.confidence)}`);
      else {
        lines.push(OUTCOMES[word.outcome] || word.outcome);
        if (word.candidate) lines.push(`Návrh z webu: ${quoted(word.candidate)} (${percent(word.confidence)})`);
      }
      if (word.queries && word.queries.length) lines.push(`Dotazy: ${word.queries.map(quoted).join(", ")}`);
      if (word.answer) lines.push(word.answer);
      for (const url of word.sources || []) lines.push(url);
      if (word.reasoning) lines.push(word.reasoning);
      if (word.model) lines.push(`${word.provider} ${word.model}`);
      lines.push(word.ids.join(", "));
      box(word.bbox, {class: `rec-word ${status.cls}`, "data-tip": lines.join("\n")}, layer);
    }
    list(host, overlay);
  }

  window.stepPreviews.lookup = async (container, project, step) => {
    container.innerHTML = `<div class="lookup-viewer"></div><div class="lookup-list"></div>`;
    const host = container.querySelector(".lookup-list");
    await pageViewer(container.querySelector(".lookup-viewer"), project, step, {
      draw: (layer, overlay) => draw(layer, overlay, host),
      describe: (overlay, page) => { if (!overlay) list(host, null); return describe(overlay, page); },
      legend: Object.values(STATUSES),
    });
  };
})();
