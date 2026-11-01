"use strict";
// Problem detection step preview: unreadable words and doubtful readings in two colours; hovering shows the
// local reading, confidence and why the word was flagged.
(function () {
  const {pageViewer, box} = window.ocrmaker;
  const KINDS = {
    unreadable: {cls: "problem-unreadable", label: "nečitelné slovo"},
    doubtful: {cls: "problem-doubtful", label: "pochybné čtení"},
  };
  const SIGNALS = {
    empty: "inkoust bez rozpoznaného textu",
    partial_text: "přečtena jen část slova",
    adjoins_empty: "navazuje na nerozpoznaný inkoust",
    low_confidence: "nízká důvěra",
    not_in_dictionary: "není ve slovníku",
    digits_in_word: "číslice uvnitř slova",
    implausible_diacritics: "nepravděpodobná diakritika",
    mixed_case: "smíšená velká a malá písmena",
    odd_symbols: "neobvyklé znaky",
    joins_next_line: "pokračuje na dalším řádku bez spojovníku",
  };
  const percent = value => `${Math.round(value)} %`;

  function describe(overlay, page) {
    if (!overlay) return `Strana ${page.index + 1}: bez výsledku detekce`;
    const {unreadable, doubtful} = overlay.counts;
    if (!unreadable && !doubtful) return `Strana ${page.index + 1}: žádné problémové místo`;
    return `Strana ${page.index + 1}: ${unreadable} nečitelných slov, ${doubtful} pochybných čtení`;
  }

  function draw(layer, overlay) {
    for (const spot of overlay.spots) {
      const kind = KINDS[spot.kind];
      const reading = spot.text ? `„${spot.text}“` : "(bez textu)";
      const why = spot.signals.map(signal => SIGNALS[signal] || signal).join(", ");
      box(spot.bbox, {class: `problem-spot ${kind.cls}`,
                      "data-tip": `${kind.label}: ${reading}\nDůvěra: ${percent(spot.confidence)}\n${why}\n${spot.id}`}, layer);
    }
  }

  window.stepPreviews.problems = (container, project, step) => pageViewer(container, project, step, {
    draw, describe, legend: Object.values(KINDS),
  });
})();
