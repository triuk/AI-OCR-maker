"use strict";
// The step preview viewer every step reuses: the working raster of a page with the step's overlay on top,
// page navigation, zoom (wheel, buttons, keys) and pan (drag). Steps draw in source page coordinates
// (see ocrmaker/pages.py); the viewer maps them onto the raster with the page's to_raster matrix.
//
//   ocrmaker.pageViewer(container, project, step, {
//     draw(layer, overlay, page, svg) {},  // add SVG shapes in source page coordinates to `layer`
//     describe(overlay, page) {},          // optional: a Czech line about the page shown in the toolbar
//     legend: [{label, cls}],              // optional: overlay classes explained under the toolbar
//   });
//
// Elements with a data-tip attribute show its text on hover; svg(tag, attrs, parent) creates elements.
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const MIN_SCALE = 0.02, MAX_SCALE = 8, STEP = 1.25;
  // The page shown last per project and step, so polling re-renders and step switches keep it.
  const remembered = {};

  function svg(tag, attrs = {}, parent = null) {
    const element = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) if (value != null) element.setAttribute(key, value);
    if (parent) parent.appendChild(element);
    return element;
  }
  // A rectangle [x0, y0, x1, y1] in source page coordinates.
  function box(bbox, attrs = {}, parent = null) {
    const [x0, y0, x1, y1] = bbox;
    return svg("rect", {x: x0, y: y0, width: x1 - x0, height: y1 - y0, ...attrs}, parent);
  }

  async function overlayOf(project, step, index) {
    try {
      return await window.ocrmaker.api(`/projects/${project.id}/steps/${step.id}/overlay/${index}`);
    } catch (error) {
      if (error.status === 404) return null;
      throw error;
    }
  }

  async function pageViewer(container, project, step, options = {}) {
    const {api, apiUrl, esc} = window.ocrmaker;
    const pages = (await api(`/projects/${project.id}/pages`)).pages;
    if (!pages.length) {
      container.innerHTML = `<p class="placeholder">Stránky zatím nejsou načtené. Nejprve dokončete Import.</p>`;
      return;
    }
    const legend = (options.legend || []).map(item => `<span class="legend-item"><i class="${esc(item.cls)}"></i>${esc(item.label)}</span>`).join("");
    container.innerHTML = `<div class="viewer">
      <div class="viewer-toolbar">
        <button data-act="prev" title="Předchozí stránka (PageUp)" aria-label="Předchozí stránka">‹</button>
        <select class="viewer-page" aria-label="Stránka">${pages.map(p => `<option value="${p.index}">Strana ${p.index + 1} z ${pages.length}</option>`).join("")}</select>
        <button data-act="next" title="Další stránka (PageDown)" aria-label="Další stránka">›</button>
        <span class="viewer-gap"></span>
        <button data-act="out" title="Oddálit (−)" aria-label="Oddálit">−</button>
        <span class="viewer-zoom" title="Měřítko vůči pracovnímu obrazu"></span>
        <button data-act="in" title="Přiblížit (+)" aria-label="Přiblížit">+</button>
        <button data-act="fit" title="Celá stránka (0)">Celá stránka</button>
        <button data-act="width" title="Na šířku okna">Na šířku</button>
        <button data-act="actual" title="Pixel obrazu na pixel obrazovky">1 : 1</button>
      </div>
      ${legend ? `<div class="viewer-legend">${legend}</div>` : ""}
      <div class="viewer-info"></div>
      <div class="viewer-stage" tabindex="0" aria-label="Náhled stránky; kolečkem myši přibližte, tažením posuňte">
        <div class="viewer-canvas"><img class="viewer-image" alt="" draggable="false"><svg class="viewer-overlay" xmlns="${NS}"></svg></div>
        <div class="viewer-tip" hidden></div>
      </div>
    </div>`;
    const $ = selector => container.querySelector(selector);
    const stage = $(".viewer-stage"), canvas = $(".viewer-canvas"), image = $(".viewer-image");
    const overlaySvg = $(".viewer-overlay"), tip = $(".viewer-tip"), select = $(".viewer-page");
    const key = `${project.id}|${step.id}`;
    let index = Math.min(remembered[key] ?? remembered[project.id] ?? 0, pages.length - 1);
    let page = null, scale = 1, tx = 0, ty = 0, mode = "fit", loading = 0;

    function apply() {
      canvas.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
      $(".viewer-zoom").textContent = `${Math.round(scale * 100)} %`;
    }
    function fit(how = "fit") {
      if (!page) return;
      mode = how;
      const {width, height} = page.raster;
      const sw = stage.clientWidth, sh = stage.clientHeight;
      scale = how === "actual" ? 1 : how === "width" ? sw / width : Math.min(sw / width, sh / height);
      scale = Math.min(Math.max(scale, MIN_SCALE), MAX_SCALE);
      tx = Math.max((sw - width * scale) / 2, 0);
      ty = how === "fit" ? Math.max((sh - height * scale) / 2, 0) : 0;
      apply();
    }
    // Zoom by a factor around a point of the stage (default: its centre), keeping that point still.
    function zoom(factor, cx = stage.clientWidth / 2, cy = stage.clientHeight / 2) {
      const next = Math.min(Math.max(scale * factor, MIN_SCALE), MAX_SCALE);
      tx = cx - (cx - tx) * next / scale;
      ty = cy - (cy - ty) * next / scale;
      scale = next; mode = null;
      apply();
    }

    async function show(next) {
      index = Math.min(Math.max(next, 0), pages.length - 1);
      remembered[key] = remembered[project.id] = index;
      select.value = String(index);
      $('[data-act="prev"]').disabled = index === 0;
      $('[data-act="next"]').disabled = index === pages.length - 1;
      page = pages[index];
      const ticket = ++loading;
      const {width, height} = page.raster;
      image.width = width; image.height = height;
      overlaySvg.setAttribute("width", width); overlaySvg.setAttribute("height", height);
      overlaySvg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      overlaySvg.replaceChildren();
      image.src = apiUrl(`/projects/${project.id}/pages/${index}/image`);
      if (mode) fit(mode); else apply();  // a page change keeps the user's own zoom and position
      const overlay = await overlayOf(project, step, index);
      if (ticket !== loading) return;
      // Everything a step draws is in source page coordinates; this group maps them onto the raster.
      const layer = svg("g", {transform: `matrix(${page.raster.to_raster.join(" ")})`, class: "overlay-layer"}, overlaySvg);
      if (overlay && options.draw) options.draw(layer, overlay, page, svg);
      $(".viewer-info").textContent = options.describe ? options.describe(overlay, page) || "" : "";
    }

    container.querySelectorAll("[data-act]").forEach(button => button.onclick = () => {
      const act = button.dataset.act;
      if (act === "prev") show(index - 1);
      else if (act === "next") show(index + 1);
      else if (act === "in") zoom(STEP);
      else if (act === "out") zoom(1 / STEP);
      else fit(act);
    });
    select.onchange = () => show(Number(select.value));
    stage.addEventListener("wheel", event => {
      event.preventDefault();
      const rect = stage.getBoundingClientRect();
      zoom(Math.pow(STEP, -Math.sign(event.deltaY)), event.clientX - rect.left, event.clientY - rect.top);
    }, {passive: false});
    stage.ondblclick = () => fit(mode === "fit" ? "actual" : "fit");
    let drag = null;
    stage.onpointerdown = event => {
      if (event.button !== 0) return;
      drag = {x: event.clientX - tx, y: event.clientY - ty};
      stage.setPointerCapture(event.pointerId);
      stage.classList.add("dragging");
    };
    stage.onpointermove = event => {
      if (drag) {
        tx = event.clientX - drag.x; ty = event.clientY - drag.y; mode = null;
        apply();
        tip.hidden = true;
        return;
      }
      const target = event.target.closest && event.target.closest("[data-tip]");
      if (!target) { tip.hidden = true; return; }
      const rect = stage.getBoundingClientRect();
      tip.textContent = target.getAttribute("data-tip");
      tip.hidden = false;
      tip.style.left = `${Math.min(event.clientX - rect.left + 14, rect.width - tip.offsetWidth - 4)}px`;
      tip.style.top = `${Math.min(event.clientY - rect.top + 16, rect.height - tip.offsetHeight - 4)}px`;
    };
    stage.onpointerup = stage.onpointercancel = () => { drag = null; stage.classList.remove("dragging"); };
    stage.onpointerleave = () => { tip.hidden = true; };
    stage.onkeydown = event => {
      const pan = 60;
      const actions = {
        "+": () => zoom(STEP), "=": () => zoom(STEP), "-": () => zoom(1 / STEP), "0": () => fit("fit"),
        PageUp: () => show(index - 1), PageDown: () => show(index + 1),
        ArrowLeft: () => { tx += pan; apply(); }, ArrowRight: () => { tx -= pan; apply(); },
        ArrowUp: () => { ty += pan; apply(); }, ArrowDown: () => { ty -= pan; apply(); },
      };
      if (!actions[event.key]) return;
      event.preventDefault(); mode = null;
      actions[event.key]();
    };
    // Keep a fitted page fitted when the window or the layout changes size.
    const resize = new ResizeObserver(() => { if (!container.isConnected) resize.disconnect(); else if (mode) fit(mode); });
    resize.observe(stage);
    await show(index);
  }

  window.ocrmaker = Object.assign(window.ocrmaker || {}, {pageViewer, svg, box});
})();
