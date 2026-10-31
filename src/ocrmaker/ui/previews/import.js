"use strict";
// Import step preview: page bounds (CropBox), the MediaBox and the scan image placement, with resolutions.
(function () {
  const {pageViewer, svg, box} = window.ocrmaker;
  const number = value => Number(value).toLocaleString("cs-CZ", {maximumFractionDigits: 1});
  const mm = points => Math.round(points / 72 * 25.4);
  const dpi = values => values[0] === values[1] || Math.abs(values[0] - values[1]) < 0.5
    ? `${number(values[0])} dpi` : `${number(values[0])} × ${number(values[1])} dpi`;
  const same = (a, b) => a && b && a.every((value, i) => Math.abs(value - b[i]) < 0.01);

  function describe(overlay, page) {
    if (!overlay) return "";
    const parts = [`Strana ${page.index + 1}: ${mm(page.width)} × ${mm(page.height)} mm (${number(page.width)} × ${number(page.height)} pt)`];
    const scan = overlay.image;
    parts.push(scan ? `sken ${scan.width} × ${scan.height} px, ${dpi(scan.dpi)}` : "bez skenu");
    const raster = overlay.raster;
    parts.push(`pracovní obraz ${raster.width} × ${raster.height} px, ${dpi(raster.dpi)}${raster.native ? " (nativní rozlišení)" : " (převzorkováno)"}`);
    if (page.rotation) parts.push(`otočení ${page.rotation}°`);
    if (!same(overlay.mediabox, overlay.bounds)) parts.push("ořez stránky (CropBox)");
    return parts.join(" · ");
  }

  function draw(layer, overlay, page) {
    const tip = describe(overlay, page);
    if (!same(overlay.mediabox, overlay.bounds)) {
      box(overlay.mediabox, {class: "import-mediabox", "data-tip": "MediaBox: celá plocha stránky v PDF"}, layer);
    }
    if (overlay.image && !same(overlay.image.bbox, overlay.bounds)) {
      box(overlay.image.bbox, {class: "import-image", "data-tip": `Sken: ${overlay.image.width} × ${overlay.image.height} px, ${dpi(overlay.image.dpi)}`}, layer);
    }
    box(overlay.bounds, {class: "import-bounds", "data-tip": tip}, layer);
    // The label sits in the page's top-left corner as displayed, sized relative to the page.
    const size = Math.max(page.width, page.height) / 55;
    const label = [`Strana ${page.index + 1}`, overlay.image ? dpi(overlay.image.dpi) : "bez skenu",
                   `${overlay.raster.width} × ${overlay.raster.height} px`].join(" · ");
    const corner = {0: [0, 0], 90: [0, page.height], 180: [page.width, page.height], 270: [page.width, 0]}[page.rotation] || [0, 0];
    const text = svg("text", {x: 0, y: 0, class: "import-label", "font-size": size, "stroke-width": size / 5, "data-tip": tip,
      transform: `translate(${corner[0]} ${corner[1]}) rotate(${-page.rotation}) translate(${size * 0.6} ${size * 1.5})`}, layer);
    text.textContent = label;
  }

  window.stepPreviews.import = (container, project, step) => pageViewer(container, project, step, {
    draw, describe,
    legend: [{label: "hranice stránky", cls: "import-bounds"}, {label: "MediaBox", cls: "import-mediabox"},
             {label: "umístění skenu", cls: "import-image"}],
  });
})();
