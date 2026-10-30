"use strict";
// AI reconstruction step preview: the words AI filled in, coloured by outcome, with the AI's confidence on
// hover: accepted reconstructions, publisher errors kept as printed, and unresolved spots that got the
// illegible marker (with the partial reading and the AI's candidate).
(function () {
  const {pageViewer, box} = window.ocrmaker;
  const STATUSES = {
    accepted: {cls: "rec-accepted", label: "doplněno AI"},
    publisher_error: {cls: "rec-publisher", label: "chyba v tisku (ponecháno)"},
    unresolved: {cls: "rec-unresolved", label: "[NEČITELNÉ]"},
  };
  const REASONS = {
    low_confidence: "AI si není dost jistá",
    no_answer: "AI místo nepřečetla",
    rejected: "odpověď AI byla odmítnuta",
    unavailable: "AI nebyla k dispozici",
  };
  const ERRORS = {recognition: "chyba rozpoznání", publisher: "chyba v tisku", none: "čtení bylo správné"};
  const percent = value => (value == null ? "–" : `${Math.round(value)} %`);
  const quoted = text => (text ? `„${text}“` : "(bez textu)");

  function describe(overlay, page) {
    if (!overlay) return `Strana ${page.index + 1}: bez výsledku rekonstrukce`;
    const {spots, resolved, markers, publisher_errors: errors} = overlay.counts;
    if (!spots) return `Strana ${page.index + 1}: žádné problémové místo`;
    return `Strana ${page.index + 1}: ${spots} problémových míst, ${resolved} doplněno, ${markers} značek [NEČITELNÉ]`
      + (errors ? `, ${errors} chyb v tisku` : "") + (overlay.rejected ? " · odpověď AI odmítnuta" : "");
  }

  function draw(layer, overlay) {
    for (const word of overlay.words) {
      const status = STATUSES[word.status];
      const lines = [`${status.label}: ${quoted(word.text)}`, `Důvěra AI: ${percent(word.confidence)}`,
                     `Místní čtení: ${quoted(word.hint)}`];
      if (word.status === "unresolved") {
        if (word.candidate) lines.push(`Návrh AI: ${quoted(word.candidate)}`);
        lines.push(REASONS[word.reason] || word.reason);
      }
      if (word.error) lines.push(ERRORS[word.error] || word.error);
      if (word.intended) lines.push(`Zamýšleno: ${quoted(word.intended)}`);
      if (word.reasoning) lines.push(word.reasoning);
      if (word.model) lines.push(`${word.provider} ${word.model}`);
      lines.push(word.ids.join(", "));
      box(word.bbox, {class: `rec-word ${status.cls}`, "data-tip": lines.join("\n")}, layer);
    }
  }

  window.stepPreviews.reconstruct = (container, project, step) => pageViewer(container, project, step, {
    draw, describe, legend: Object.values(STATUSES),
  });
})();
