"use strict";
// Local OCR step preview: every word box coloured by Tesseract's confidence; hovering shows text and confidence.
(function () {
  const {pageViewer, box} = window.ocrmaker;
  // Confidence bands, best first: [lowest confidence, CSS class, Czech legend label].
  const BANDS = [[90, "ocr-high", "důvěra 90–100 %"], [75, "ocr-good", "75–90 %"], [50, "ocr-low", "50–75 %"],
                 [0, "ocr-poor", "pod 50 %"]];
  const band = confidence => BANDS.find(([lowest]) => confidence >= lowest)[1];
  // An empty word box is ink Tesseract left unread (ocrmaker/inkgaps.py); it has no text and no confidence.
  const EMPTY = ["ocr-empty", "prázdný rámeček slova"];
  const percent = value => `${Math.round(value)} %`;

  function describe(overlay, page) {
    if (!overlay) return `Strana ${page.index + 1}: bez výsledku rozpoznávání`;
    const words = overlay.words.filter(word => word.text);
    const empty = overlay.words.length - words.length;
    if (!words.length) return `Strana ${page.index + 1}: žádné rozpoznané slovo` + (empty ? `, ${empty} prázdných rámečků` : "");
    const mean = words.reduce((sum, word) => sum + word.confidence, 0) / words.length;
    const weak = words.filter(word => word.confidence < 75).length;
    return `Strana ${page.index + 1}: ${words.length} slov, ${overlay.lines.length} řádků, průměrná důvěra ${percent(mean)}`
      + (weak ? `, ${weak} slov pod 75 %` : "") + (empty ? `, ${empty} prázdných rámečků` : "");
  }

  function draw(layer, overlay) {
    for (const word of overlay.words) {
      if (!word.text) {
        box(word.bbox, {class: `ocr-word ${EMPTY[0]}`, "data-tip": `Inkoust bez rozpoznaného textu\n${word.id}`}, layer);
        continue;
      }
      box(word.bbox, {class: `ocr-word ${band(word.confidence)}`,
                      "data-tip": `„${word.text}“\nDůvěra: ${percent(word.confidence)}\n${word.id}`}, layer);
    }
  }

  window.stepPreviews.ocr = (container, project, step) => pageViewer(container, project, step, {
    draw, describe, legend: [...BANDS.map(([, cls, label]) => ({label, cls})), {label: EMPTY[1], cls: EMPTY[0]}],
  });
})();
