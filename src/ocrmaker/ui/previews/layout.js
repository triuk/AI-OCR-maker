"use strict";
// Layout step preview: every block outlined and coloured by its kind, with its number; hovering shows the
// block's kind, size and first words.
(function () {
  const {pageViewer, svg, box} = window.ocrmaker;
  // [kind, Czech legend label]; the CSS class is `layout-<kind>`.
  const KINDS = [["body", "text"], ["heading", "nadpis"], ["caption", "popisek"], ["header", "záhlaví"],
                 ["footer", "zápatí"], ["table", "tabulka"], ["ad", "inzerát"]];
  const LABELS = Object.fromEntries(KINDS);

  // A point in source page coordinates on the raster, where labels stay upright whatever the page rotation.
  const toRaster = (page, x, y) => {
    const [a, b, c, d, e, f] = page.raster.to_raster;
    return [a * x + c * y + e, b * x + d * y + f];
  };
  // The top-left corner of a box as displayed.
  function corner(page, bbox) {
    const points = [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[0], bbox[3]], [bbox[2], bbox[3]]]
      .map(([x, y]) => toRaster(page, x, y));
    return [Math.min(...points.map(p => p[0])), Math.min(...points.map(p => p[1]))];
  }
  // Number badges are sized relative to the raster so they read the same at any resolution.
  const badgeSize = page => Math.max(page.raster.width, page.raster.height) / 70;

  function badge(root, page, bbox, text, cls, tip) {
    const size = badgeSize(page);
    const [x, y] = corner(page, bbox);
    const group = svg("g", {class: `block-badge ${cls}`, "data-tip": tip}, root);
    svg("rect", {x: x - size * 0.2, y: y - size * 0.2, width: size * (0.55 * String(text).length + 0.6),
                 height: size * 1.15, rx: size * 0.2}, group);
    svg("text", {x: x + size * 0.1, y: y + size * 0.72, "font-size": size}, group).textContent = text;
    return group;
  }

  function describe(overlay, page) {
    if (!overlay) return `Strana ${page.index + 1}: bez rozvržení`;
    if (!overlay.blocks.length) return `Strana ${page.index + 1}: žádný blok`;
    const counts = {};
    for (const block of overlay.blocks) counts[block.kind] = (counts[block.kind] || 0) + 1;
    const parts = KINDS.filter(([kind]) => counts[kind]).map(([kind, label]) => `${label} ${counts[kind]}`);
    return `Strana ${page.index + 1}: ${overlay.blocks.length} bloků, ${overlay.columns.length} sloupců · ${parts.join(", ")}`;
  }

  function draw(layer, overlay, page) {
    const root = layer.parentNode;
    for (const column of overlay.columns) box(column, {class: "layout-column"}, layer);
    for (const block of overlay.blocks) {
      const tip = `Blok ${block.number}: ${LABELS[block.kind] || block.kind}\n${block.lines} řádků, ${block.words} slov\n„${block.text}…“\n${block.id}`;
      box(block.bbox, {class: `layout-block layout-${block.kind}`, "data-tip": tip}, layer);
      badge(root, page, block.bbox, block.number, `layout-${block.kind}`, tip);
    }
  }

  window.stepPreviews.layout = (container, project, step) => pageViewer(container, project, step, {
    draw, describe,
    legend: KINDS.filter(([kind]) => kind !== "ad").map(([kind, label]) => ({label, cls: `layout-${kind}`}))
      .concat([{label: "sloupec", cls: "layout-column"}]),
  });
})();
