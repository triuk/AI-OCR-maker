"use strict";
// Layout (regions) step preview: the page's layout regions in reading order, outlined by their article's kind and
// labelled 3a, 3b (article 3, its first and second column); ink in no region dashed red (ADR 0006).
(function () {
  const {pageViewer, svg, box} = window.ocrmaker;
  // [kind, Czech legend label]; the CSS classes are the Layout preview's `layout-<kind>`.
  const KINDS = [["article", "článek"], ["header", "záhlaví"], ["footer", "zápatí"], ["ad", "inzerát"]];
  const LABELS = Object.fromEntries(KINDS);
  const CLASSES = {article: "layout-body", header: "layout-header", footer: "layout-footer", ad: "layout-ad"};
  const COLUMNS = "abcdefghijklmnopqrstuvwxyz";

  const toRaster = (page, x, y) => {
    const [a, b, c, d, e, f] = page.raster.to_raster;
    return [a * x + c * y + e, b * x + d * y + f];
  };
  function corner(page, bbox) {
    const points = [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[0], bbox[3]], [bbox[2], bbox[3]]]
      .map(([x, y]) => toRaster(page, x, y));
    return [Math.min(...points.map(p => p[0])), Math.min(...points.map(p => p[1]))];
  }
  const badgeSize = page => Math.max(page.raster.width, page.raster.height) / 70;

  function badge(root, page, bbox, text, cls, tip) {
    const size = badgeSize(page);
    const [x, y] = corner(page, bbox);
    const group = svg("g", {class: `block-badge ${cls}`, "data-tip": tip}, root);
    svg("rect", {x: x - size * 0.2, y: y - size * 0.2, width: size * (0.55 * String(text).length + 0.6),
                 height: size * 1.15, rx: size * 0.2}, group);
    svg("text", {x: x + size * 0.1, y: y + size * 0.72, "font-size": size}, group).textContent = text;
  }

  const label = region => `${region.article}${COLUMNS[region.column - 1] || region.column}`;

  function describe(overlay, page) {
    if (!overlay) return `Strana ${page.index + 1}: bez oblastí (metoda bloků)`;
    const articles = new Set(overlay.regions.map(region => region.article)).size;
    const source = overlay.source === "failed" ? " · bez použitelné odpovědi AI: celá strana je jedna oblast" : "";
    const issues = overlay.problems.length ? ` · problémů kontroly ${overlay.problems.length}` : "";
    return `Strana ${page.index + 1}: ${overlay.regions.length} oblastí, ${articles} částí${issues}${source}`;
  }

  function draw(layer, overlay, page) {
    const root = layer.parentNode;
    for (const region of overlay.regions) {
      const cls = CLASSES[region.kind] || "layout-body";
      const turned = region.orientation !== "0" ? `\nOrientace: ${region.orientation}` : "";
      const tip = `Oblast ${label(region)}: ${LABELS[region.kind] || region.kind}\n${region.title || ""}${turned}`;
      box(region.bbox, {class: `layout-block ${cls}`, "data-tip": tip}, layer);
      badge(root, page, region.bbox, label(region), cls, tip);
    }
    for (const spot of overlay.spots) box(spot, {class: "regions-spot", "data-tip": "Inkoust mimo oblasti"}, layer);
  }

  window.stepPreviews.regions = (container, project, step) => pageViewer(container, project, step, {
    draw, describe,
    legend: KINDS.map(([kind, text]) => ({label: text, cls: CLASSES[kind]}))
      .concat([{label: "inkoust mimo oblasti", cls: "regions-spot"}]),
  });
})();
