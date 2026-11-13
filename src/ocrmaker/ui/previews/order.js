"use strict";
// Reading order step preview: the articles numbered in reading order, each in its own colour, with arrows
// leading from block to block in the order the text is read.
(function () {
  const {pageViewer, svg, box} = window.ocrmaker;
  const GROUPS = {article: "článek", header: "záhlaví", footer: "zápatí", ad: "inzerát"};
  const PALETTE = 8;  // article colours order-1 … order-8 repeat after this many articles

  const toRaster = (page, x, y) => {
    const [a, b, c, d, e, f] = page.raster.to_raster;
    return [a * x + c * y + e, b * x + d * y + f];
  };
  const centre = (page, bbox) => toRaster(page, (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2);
  function corner(page, bbox) {
    const points = [[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[0], bbox[3]], [bbox[2], bbox[3]]]
      .map(([x, y]) => toRaster(page, x, y));
    return [Math.min(...points.map(p => p[0])), Math.min(...points.map(p => p[1]))];
  }
  const colour = item => item.kind === "article" ? `order-${(item.number - 1) % PALETTE + 1}` : `order-${item.kind}`;

  function describe(overlay, page) {
    if (!overlay) return `Strana ${page.index + 1}: bez pořadí čtení`;
    const articles = overlay.articles.filter(item => item.kind === "article").length;
    const source = {regions: "pořadí z rozvržení", empty: "bez textu"}[overlay.source] || overlay.source;
    const continued = overlay.articles.filter(item => item.continues).map(item => `${item.number} navazuje na ${item.continues}`);
    return `Strana ${page.index + 1}: ${articles} článků, ${source}` + (continued.length ? ` · ${continued.join(", ")}` : "")
      + (overlay.reason ? ` · nepropojeno: ${overlay.reason}` : "");
  }

  function draw(layer, overlay, page) {
    const root = layer.parentNode;
    const size = Math.max(page.raster.width, page.raster.height) / 60;
    let defs = root.querySelector("defs");
    if (!defs) {
      defs = svg("defs", {}, root);
      const marker = svg("marker", {id: "order-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7,
                                    markerHeight: 7, orient: "auto-start-reverse", markerUnits: "strokeWidth"}, defs);
      svg("path", {d: "M0,0 L10,5 L0,10 z", class: "order-arrow-head"}, marker);
    }
    for (const item of overlay.articles) {
      const cls = colour(item);
      const label = `${GROUPS[item.kind] || item.kind} ${item.number}`;
      const tip = `${label} (${item.id})\nBloky v pořadí: ${item.blocks.map(block => block.number).join(" → ")}`
        + (item.continues ? `\nNavazuje na ${item.continues} z předchozí strany` : "");
      for (const block of item.blocks) box(block.bbox, {class: `order-block ${cls}`, "data-tip": tip}, layer);
      // Arrows from each block's centre to the next one's, in raster pixels so they stay upright and sharp.
      for (let i = 1; i < item.blocks.length; i++) {
        const [x1, y1] = centre(page, item.blocks[i - 1].bbox), [x2, y2] = centre(page, item.blocks[i].bbox);
        svg("line", {x1, y1, x2, y2, class: `order-arrow ${cls}`, "marker-end": "url(#order-arrow)"}, root);
      }
      const [x, y] = corner(page, item.blocks[0].bbox);
      const text = item.continues ? `${item.number} ↩` : String(item.number);
      const group = svg("g", {class: `order-badge ${cls}`, "data-tip": tip}, root);
      svg("circle", {cx: x, cy: y, r: size * 0.85}, group);
      svg("text", {x, y: y + size * 0.36, "font-size": size, "text-anchor": "middle"}, group).textContent = text;
    }
  }

  window.stepPreviews.order = (container, project, step) => pageViewer(container, project, step, {
    draw, describe,
    legend: [{label: "článek (číslo = pořadí čtení, šipky mezi bloky)", cls: "order-1"},
             {label: "záhlaví", cls: "order-header"}, {label: "zápatí", cls: "order-footer"},
             {label: "inzerát", cls: "order-ad"}],
  });
})();
