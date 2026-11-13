"use strict";
// Layout (regions) step: the layout editor, the manual half of Layout (ADR 0006, issue #20). All pages lie under
// each other in one scroller; the person moves, resizes, draws, orders, locks and notes layout regions, every edit is
// saved to the server at once (the server is the only store) and "Zpracovat" sends the unlocked pages to the AI
// again, "Schválit" approves the layout and the run goes on. It is a port of the throwaway prototype in
// benchmark/prototypes/regions/editor/ (not in git); the interaction code is kept as it was approved.
//
// Geometry: boxes are [x0, y0, x1, y1] in the 0..1000 grid (a fraction of the whole page on both axes), so the page
// image maps to them directly. A page is one row of regions in reading order; consecutive regions with the same
// `part` form one part (article column 3a, 3b ...). Part properties (kind, title, continues-previous) live in
// `parts[part]`. The server's page layout is a list of parts ({kind, title, continues, regions: [...]}); the two
// conversions are `fromServer` and `toServer`.
//
// A project without layout regions (the blocks method) keeps the read-only overlay preview (`legacy` below).
(function () {
  const {pageViewer, svg, box, api, apiUrl, esc, plural, toast} = window.ocrmaker;

  // ------------------------------------------------------------------ read-only overlay (blocks method)
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

  const regionLabel = region => `${region.article}${COLUMNS[region.column - 1] || region.column}`;

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
      const tip = `Oblast ${regionLabel(region)}: ${LABELS[region.kind] || region.kind}\n${region.title || ""}${turned}`;
      box(region.bbox, {class: `layout-block ${cls}`, "data-tip": tip}, layer);
      badge(root, page, region.bbox, regionLabel(region), cls, tip);
    }
    for (const spot of overlay.spots) box(spot, {class: "regions-spot", "data-tip": "Inkoust mimo oblasti"}, layer);
  }

  const legacy = (container, project, step) => pageViewer(container, project, step, {
    draw, describe,
    legend: KINDS.map(([kind, text]) => ({label: text, cls: CLASSES[kind]}))
      .concat([{label: "inkoust mimo oblasti", cls: "regions-spot"}]),
  });

  // ------------------------------------------------------------------ editor
  const GRID = 1000, MIN = 8, OVERLAP_MIN = 3, EDGE = 6, SLOP = 4, SAVE_DELAY = 300, NOTE_DELAY = 600;
  const KIND_KEYS = ["article", "header", "footer", "ad"];
  const KIND_NAME = {article: "článek", header: "záhlaví", footer: "patička", ad: "inzerát"};
  const ROTATIONS = ["0", "90", "180", "270"];
  const NS = "http://www.w3.org/2000/svg";
  const ZOOM_KEY = "ocrmaker.layout.zoom";

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const r2 = v => Math.round(v * 100) / 100;
  const count = (n, forms) => `${n} ${n === 1 ? forms[0] : n >= 2 && n <= 4 ? forms[1] : forms[2]}`;
  const PROBLEMS = ["problém", "problémy", "problémů"];
  const UNLOCKED = ["odemčený box", "odemčené boxy", "odemčených boxů"];
  const CANCELLED = Symbol("cancelled");
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* private window */ } },
  };
  const center = b => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
  function intersection(a, b) {
    const x0 = Math.max(a[0], b[0]), y0 = Math.max(a[1], b[1]), x1 = Math.min(a[2], b[2]), y1 = Math.min(a[3], b[3]);
    return x1 > x0 && y1 > y0 ? [x0, y0, x1, y1] : null;
  }

  // A page state: {regions: [{id, part, box, orientation, locked}], parts: {id: {kind, title, cont}}, nid, pnid, note}.
  // Boxes keep exactly the values the server sent (it compares them); only values the person changes are rounded.
  function fromServer(parts, note = "", locked = null) {
    const ps = {regions: [], parts: {}, nid: 1, pnid: 1, note};
    for (const part of parts) {
      const pid = ps.pnid++;
      ps.parts[pid] = {kind: part.kind, title: part.title || "", cont: !!part.continues};
      for (const region of part.regions) {
        ps.regions.push({id: ps.nid++, part: pid, box: region.box.slice(), orientation: String(region.orientation),
                         locked: locked ?? region.locked !== false});
      }
    }
    return ps;
  }
  function toServer(ps) {
    const parts = [];
    for (const r of ps.regions) {
      let last = parts[parts.length - 1];
      if (!last || last.id !== r.part) {
        const p = ps.parts[r.part];
        // "continues" has no meaning on a page's first part (the server ignores it)
        last = {id: r.part, kind: p.kind, title: p.title, continues: !!p.cont && parts.length > 0, regions: []};
        parts.push(last);
      }
      last.regions.push({box: r.box.slice(), orientation: r.orientation, locked: r.locked});
    }
    return parts.map(({id, ...part}) => part);
  }
  const layoutOf = ps => JSON.stringify({regions: ps.regions, parts: ps.parts, nid: ps.nid, pnid: ps.pnid});
  function restore(ps, text) { Object.assign(ps, JSON.parse(text)); }

  // Labels: part ordinal and, for parts of several regions, the column letter (3a, 3b).
  function labels(regions) {
    const of = {}, counts = {};
    let ordinal = 0, prev = null, index = 0;
    for (const r of regions) {
      if (r.part !== prev) { ordinal++; index = 0; prev = r.part; }
      counts[r.part] = (counts[r.part] || 0) + 1;
      of[r.id] = {ordinal, index: index++, part: r.part};
    }
    for (const r of regions) {
      const o = of[r.id];
      o.count = counts[r.part];
      o.label = o.count > 1 ? `${o.ordinal}${o.index < 26 ? String.fromCharCode(97 + o.index) : "·" + o.index}` : String(o.ordinal);
    }
    return of;
  }
  function signature(ps) {
    const lab = labels(ps.regions);
    return JSON.stringify(ps.regions.map(r => [r.box.map(v => Math.round(v * 10) / 10), r.orientation, r.locked,
      lab[r.id].ordinal, ps.parts[r.part].kind, !!ps.parts[r.part].cont && lab[r.id].ordinal > 1]));
  }
  const regionOf = (ps, id) => ps.regions.find(r => r.id === id);
  const indexOf = (ps, id) => ps.regions.findIndex(r => r.id === id);
  function pruneParts(ps) {
    const used = new Set(ps.regions.map(r => r.part));
    for (const id of Object.keys(ps.parts)) if (!used.has(+id)) delete ps.parts[id];
  }
  const unlockedOf = ps => ps.regions.filter(r => !r.locked);

  // Resize the edge (axis a: 0 = x, 1 = y; side s: 0 = min, 1 = max) of box `id` by delta. With `yieldNeighbours`
  // the neighbours across that edge give way, but only those the edge actually reaches. Returns the new boxes.
  function resizeEdge(boxes, id, a, s, delta, yieldNeighbours) {
    const res = {};
    for (const k of Object.keys(boxes)) res[k] = boxes[k].slice();
    const B = boxes[id], b = 1 - a, i = a + 2 * s;
    let v = clamp(B[i] + delta, 0, GRID);
    v = s === 0 ? Math.min(v, B[a + 2] - MIN) : Math.max(v, B[a] + MIN);
    if (yieldNeighbours) {
      // Only a neighbour the edge actually reaches gives way (its near edge is pushed ahead of the dragged edge);
      // neighbours that already overlapped the box before the drag are left alone.
      for (const k of Object.keys(boxes)) {
        if (+k === id) continue;
        const N = boxes[k];
        if (Math.min(N[b + 2], B[b + 2]) - Math.max(N[b], B[b]) <= 0) continue;     // not beside the edge
        if (s === 1) {
          if (N[a] < B[a + 2] - OVERLAP_MIN || N[a + 2] <= B[a + 2]) continue;      // not beyond the max edge
          v = Math.min(v, N[a + 2] - MIN);
          res[k][a] = Math.max(N[a], v);
        } else {
          if (N[a + 2] > B[a] + OVERLAP_MIN || N[a] >= B[a]) continue;
          v = Math.max(v, N[a] + MIN);
          res[k][a + 2] = Math.min(N[a + 2], v);
        }
      }
      // v may have been limited by a later neighbour: recompute the pushed edges with the final v
      for (const k of Object.keys(boxes)) {
        if (+k === id) continue;
        const N = boxes[k];
        if (Math.min(N[b + 2], B[b + 2]) - Math.max(N[b], B[b]) <= 0) continue;
        if (s === 1 && N[a] >= B[a + 2] - OVERLAP_MIN && N[a + 2] > B[a + 2]) res[k][a] = Math.max(N[a], v);
        if (s === 0 && N[a + 2] <= B[a] + OVERLAP_MIN && N[a] < B[a]) res[k][a + 2] = Math.min(N[a + 2], v);
      }
    }
    res[id][i] = v;
    return res;
  }
  function resizeBoxes(boxes, id, edges, dx, dy, shift) {
    let res = boxes;
    if (edges.x) res = resizeEdge(res, id, 0, edges.x === "max" ? 1 : 0, dx, shift);
    if (edges.y) res = resizeEdge(res, id, 1, edges.y === "max" ? 1 : 0, dy, shift);
    return res;
  }
  // Move box `id` by (dx, dy); with `shift` the boxes in its way are pushed along the dominant axis.
  function moveBoxes(boxes, id, dx, dy, shift) {
    const res = {};
    for (const k of Object.keys(boxes)) res[k] = boxes[k].slice();
    const B = boxes[id], w = B[2] - B[0], h = B[3] - B[1];
    const x = clamp(B[0] + dx, 0, GRID - w), y = clamp(B[1] + dy, 0, GRID - h);
    res[id] = [x, y, x + w, y + h];
    if (!shift || (dx === 0 && dy === 0)) return res;
    const a = Math.abs(dx) >= Math.abs(dy) ? 0 : 1, dir = Math.sign(a === 0 ? dx : dy);
    const seen = new Set([id]);
    const start = boxes[id];
    const push = pusher => {
      for (const k of Object.keys(res)) {
        if (seen.has(+k)) continue;
        const N = res[k], x2 = intersection(pusher, N);
        if (pusher === res[id] && intersection(start, boxes[k])) continue;   // already overlapped before the drag
        if (!x2 || x2[2] - x2[0] < 0.5 || x2[3] - x2[1] < 0.5) continue;
        if ((N[a] + N[a + 2]) / 2 * dir <= (pusher[a] + pusher[a + 2]) / 2 * dir) continue;   // behind the pusher
        const size = N[a + 2] - N[a], at = dir > 0 ? pusher[a + 2] : pusher[a] - size;
        if (at < 0 || at + size > GRID) return false;
        seen.add(+k);
        const moved = N.slice();
        moved[a] = at; moved[a + 2] = at + size;
        res[k] = moved;
        if (!push(moved)) return false;
      }
      return true;
    };
    return push(res[id]) ? res : null;
  }

  // The reading order of a page after order-mode clicks: the clicked regions in click order (a Shift+click joins the
  // previous part), then the unclicked ones in their current order.
  function applyOrder(ps, clicks) {
    const byId = new Map(ps.regions.map(r => [r.id, r]));
    const parts = {}, out = [];
    let next = ps.pnid, current = null;
    for (const c of clicks) {
      const r = byId.get(c.id);
      if (!c.join || current === null) {
        current = next++;
        const old = ps.parts[r.part], first = ps.regions.find(x => x.part === r.part) === r;
        parts[current] = {kind: old.kind, title: old.title, cont: first && old.cont};
      }
      out.push({...r, part: current});
    }
    const clicked = new Set(clicks.map(c => c.id));
    for (const r of ps.regions) {
      if (clicked.has(r.id)) continue;
      out.push(r);
      parts[r.part] ??= ps.parts[r.part];
    }
    return {regions: out, parts, pnid: next};
  }

  const hueOf = ordinal => (ordinal * 137.5 + 15) % 360;
  function tools(r, selected, g) {
    const [x0, y0, x1, y1] = r.box, sx = g.W / GRID, sy = g.H / GRID;
    const px0 = x0 * sx, py0 = y0 * sy, px1 = x1 * sx, py1 = y1 * sy;
    const inside = px1 - px0 >= 76 && py1 - py0 >= 28;
    if (!inside && !selected) return null;
    let y = inside ? py0 + 3 : py0 - 19;
    if (!inside && py0 < 20) y = py1 + 3;
    const lockX = inside ? px1 - 19 : px1 - 16;
    return {lock: {x: lockX, y, s: 16}, orient: {x: lockX - 19, y, s: 16}};
  }
  const within = (t, px, py) => px >= t.x && px <= t.x + t.s && py >= t.y && py <= t.y + t.s;

  function orientIcon(o, x, y) {
    const cx = x + 8, cy = y + 8;
    if (o === "stacked") return `<path d="M5 4h6M5 8h6M5 12h6M8 2v0" stroke="#222" stroke-width="2" stroke-linecap="round" transform="translate(${x},${y})"/>`;
    const turn = {"0": 0, "90": -90, "180": 180, "270": 90}[o] ?? 0;   // direction the text runs on the page
    return `<g transform="translate(${cx},${cy}) rotate(${turn})"><path d="M-5 0H4M1 -3.5L4.5 0L1 3.5" fill="none" stroke="#222" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></g>`;
  }
  function lockIcon(locked, x, y) {
    return `<g transform="translate(${x},${y})"><rect x="3" y="7.5" width="10" height="6.5" rx="1.5" fill="#222"/>
      <path d="${locked ? "M5.2 7.5V5.2a2.8 2.8 0 0 1 5.6 0V7.5" : "M5.2 7.5V5.2a2.8 2.8 0 0 1 5.4-1"}" fill="none" stroke="#222" stroke-width="1.7" stroke-linecap="round"/></g>`;
  }
  const cursorFor = h => {
    if (!h) return "crosshair";
    if (h.type === "lock" || h.type === "orient") return "pointer";
    if (h.type === "move") return "move";
    const {x, y} = h.edges;
    if (x && y) return (x === y) ? "nwse-resize" : "nesw-resize";
    return x ? "ew-resize" : "ns-resize";
  };

  const HELP = `<h3>Zkratky</h3><table>
    <tr class="sect"><td colspan="2">Pohyb</td></tr>
    <tr><td><kbd>kolečko</kbd></td><td>posun po stranách</td></tr>
    <tr><td><kbd>Ctrl</kbd> + <kbd>kolečko</kbd></td><td>zoom ke kurzoru</td></tr>
    <tr><td><kbd>mezerník</kbd> + tažení, prostřední tlačítko</td><td>posun plochy</td></tr>
    <tr><td><kbd>N</kbd> / <kbd>Shift</kbd>+<kbd>N</kbd></td><td>další / předchozí problém</td></tr>
    <tr class="sect"><td colspan="2">Boxy</td></tr>
    <tr><td>tažení boxu / hrany / rohu</td><td>posun / změna velikosti (bez přichycování)</td></tr>
    <tr><td><kbd>Shift</kbd> při tažení</td><td>soused uhne (hranice sloupců se posune, box se odsune); bez Shiftu je překryv jen varování</td></tr>
    <tr><td>tažení na prázdném místě</td><td>nový box: za vybraný jako další sloupec téže části, bez výběru nová část pod boxem nad ním</td></tr>
    <tr><td><kbd>Del</kbd></td><td>smazat vybraný box</td></tr>
    <tr><td><kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd></td><td>zpět / znovu (po stranách)</td></tr>
    <tr><td><kbd>L</kbd> nebo ikonka zámku</td><td>zamčeno (plná čára) / odemčeno (čárkovaně, výrazná barva); nový box je zamčený</td></tr>
    <tr><td><kbd>1</kbd> <kbd>2</kbd> <kbd>3</kbd> <kbd>4</kbd></td><td>článek / záhlaví / patička / inzerát</td></tr>
    <tr><td><kbd>R</kbd> nebo šipka na boxu</td><td>otočit o 90° (<kbd>Shift</kbd>+<kbd>R</kbd> = písmena pod sebou)</td></tr>
    <tr class="sect"><td colspan="2">Pořadí čtení</td></tr>
    <tr><td><kbd>O</kbd></td><td>režim Pořadí: klik = další část, <kbd>Shift</kbd>+klik = další sloupec téže části, <kbd>Backspace</kbd> krok zpět, <kbd>Enter</kbd>/<kbd>Esc</kbd> konec</td></tr>
    <tr><td><kbd>Alt</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd></td><td>posunout vybraný box o místo v pořadí</td></tr>
    <tr><td><kbd>J</kbd></td><td>část pokračuje předchozí částí (↪)</td></tr>
    <tr><td><kbd>A</kbd></td><td>zobrazit / skrýt šipky pořadí</td></tr>
    <tr class="sect"><td colspan="2">Jiné</td></tr>
    <tr><td><kbd>?</kbd></td><td>tato nápověda · <kbd>Esc</kbd> zavře</td></tr></table>
    <div class="legend"><span>Štítek <b>3a</b> = část 3, 1. sloupec</span><span>plná šipka = uvnitř části, čárkovaná = mezi částmi</span><span>barva = část, černý štítek = druh</span></div>`;

  async function mount(container, project) {
    const base = `/projects/${project.id}/layout`;
    let srv = await api(base);   // the server's view: state, round, running, kept, pages; 422 when there are no regions
    if (!container.isConnected) return true;

    container.innerHTML = `<div class="layed">
      <header class="le-top">
        <span class="le-summary"></span><span class="le-gap"></span>
        <span class="le-zoombox"><button type="button" data-act="zout" title="Oddálit (Ctrl + kolečko)">−</button><button type="button" data-act="zfit" title="Celá šířka okna"></button><button type="button" data-act="zin" title="Přiblížit (Ctrl + kolečko)">+</button></span>
        <button type="button" data-act="order" title="Zadat pořadí čtení klikáním (O)">Pořadí <kbd>O</kbd></button>
        <button type="button" data-act="process" title="Co by se poslalo AI">Zpracovat</button>
        <button type="button" data-act="approve" class="primary" title="Schválit rozvržení celého dokumentu">Schválit</button>
        <button type="button" data-act="help" title="Zkratky (?)">?</button>
      </header>
      <div class="le-status" hidden></div>
      <div class="le-wrap">
        <div class="le-orderbar" hidden></div>
        <div class="le-scroller" tabindex="-1"><div class="le-pages"></div></div>
        <div class="le-process le-panel" hidden></div>
        <div class="le-help le-panel" hidden></div>
        <div class="le-toast" hidden></div>
      </div>
    </div>`;
    const root = container.firstElementChild;
    const $ = selector => root.querySelector(selector);
    const button = act => $(`[data-act="${act}"]`);
    const scroller = $(".le-scroller"), pagesEl = $(".le-pages");
    $(".le-help").innerHTML = HELP;

    // ---------------------------------------------------------------- state
    let S = [];                       // per page state (see fromServer)
    let saved = [];                   // per page: the parts the server holds
    let proposalSig = [];             // per page: the signature of the AI's proposal
    let info = [];                    // per page {el, canvas, svg, img, W, H}
    let zoom = clamp(Number(store.get(ZOOM_KEY)) || 1, 0.3, 8);
    let sel = null, hover = null, space = false, ptr = null, orderMode = null, flash = null;
    let undoStack = [], redoStack = [], problemCursor = null, arrows = true, panel = null, toastTimer = 0;
    let readOnly = !!project.running || !!srv.running, wasRunning = readOnly, approvalAccepted = false;
    let reloading = false, disposed = false;
    const dirty = new Set(), noteDirty = new Set(), timers = {}, noteTimers = {}, chains = {};
    let inflight = 0;

    const pageNo = i => srv.pages[i].index;
    const isEdited = i => signature(S[i]) !== proposalSig[i];
    const selectedRegion = () => sel && S[sel.page] ? regionOf(S[sel.page], sel.id) : null;
    const isActive = () => !disposed && root.isConnected && root.getClientRects().length > 0;

    // Problems of a page: AI ink spots not covered by any region (centre rule) and overlapping region pairs.
    function problems(i) {
      const ps = S[i], list = [];
      for (let a = 0; a < ps.regions.length; a++) {
        for (let b = a + 1; b < ps.regions.length; b++) {
          const x = intersection(ps.regions[a].box, ps.regions[b].box);
          if (x && x[2] - x[0] >= OVERLAP_MIN && x[3] - x[1] >= OVERLAP_MIN) list.push({page: i, type: "overlap", rect: x, ids: [ps.regions[a].id, ps.regions[b].id]});
        }
      }
      for (const spot of srv.pages[i].spots) {
        const [cx, cy] = center(spot);
        if (!ps.regions.some(r => cx >= r.box[0] && cx <= r.box[2] && cy >= r.box[1] && cy <= r.box[3])) list.push({page: i, type: "spot", rect: spot});
      }
      return list;
    }
    const allProblems = () => srv.pages.flatMap((_, i) => problems(i)).sort((a, b) => a.page - b.page || a.rect[1] - b.rect[1] || a.rect[0] - b.rect[0]);

    function hint(text) {
      const el = $(".le-toast");
      el.textContent = text; el.hidden = false;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
    }

    // ---------------------------------------------------------------- saving (the server is the store)
    // Editing an approved layout withdraws the approval and the results of the later steps; ask once first.
    function discardMessage(n) {
      return `Úprava zruší schválení rozvržení a výsledky dalších kroků.${n ? ` Zahodí se i ${n} ${plural(n, "ruční oprava", "ruční opravy", "ručních oprav")}.` : ""}\n\nPokračovat?`;
    }
    function allowEdit() {
      if (readOnly) { hint("Probíhá zpracování, rozvržení je teď jen ke čtení."); return false; }
      if (srv.state !== "approved" || approvalAccepted) return true;
      if (!confirm(discardMessage(srv.discards))) return false;
      approvalAccepted = true;
      return true;
    }
    // PUT that sends the number of manual corrections it discards after approval; the server answers 409 with the
    // current number when it changed (another tab), then the person is asked again.
    async function put(path, body) {
      if (srv.state === "approved") body.discard_corrections = srv.discards;
      try {
        return await api(`${base}${path}`, "PUT", body);
      } catch (e) {
        if (e.status !== 409 || !e.data || typeof e.data.corrections !== "number") throw e;
        if (!confirm(discardMessage(e.data.corrections))) throw CANCELLED;
        srv.discards = body.discard_corrections = e.data.corrections;
        return api(`${base}${path}`, "PUT", body);
      }
    }
    function applyMeta(i, view) {
      Object.assign(srv.pages[i], {round: view.round, source: view.source, reason: view.reason, sent_note: view.sent_note,
                                   process: view.process});
    }
    // The server took an edit of page i: its answer is the page's view; the approval, if any, is withdrawn.
    function savedView(i, view) {
      applyMeta(i, view);
      if (srv.state === "approved") {
        srv.state = "waiting"; srv.discards = 0; approvalAccepted = false;
        hint("Úprava zrušila schválení rozvržení.");
        window.ocrmaker.refresh();
      }
      updateTop();
    }
    function scheduleSave(i) {
      dirty.add(i);
      clearTimeout(timers[i]);
      timers[i] = setTimeout(() => persist(i), SAVE_DELAY);
    }
    function scheduleNote(i) {
      noteDirty.add(i);
      clearTimeout(noteTimers[i]);
      noteTimers[i] = setTimeout(() => persist(i), NOTE_DELAY);
    }
    // One page's saves run one after another: its layout, then its note.
    function persist(i) {
      clearTimeout(timers[i]); clearTimeout(noteTimers[i]);
      chains[i] = (chains[i] || Promise.resolve()).then(() => saveNow(i));
      return chains[i];
    }
    async function saveNow(i) {
      inflight++;
      try {
        if (dirty.delete(i)) {
          try {
            const view = await put(`/pages/${pageNo(i)}`, {parts: toServer(S[i])});
            saved[i] = view.parts;
            savedView(i, view);
          } catch (e) {
            approvalAccepted = false;
            if (e !== CANCELLED) toast(e.message);
            revertPage(i);
          }
        }
        if (noteDirty.delete(i)) {
          try {
            savedView(i, await put(`/pages/${pageNo(i)}/note`, {text: S[i].note}));
          } catch (e) {
            approvalAccepted = false;
            if (e !== CANCELLED) toast(e.message);
          }
        }
      } finally {
        inflight--;
        if (!disposed && info[i]) renderNotices(i);
      }
    }
    // The server did not take the page's layout: show what it holds.
    function revertPage(i) {
      if (disposed) return;
      S[i] = fromServer(saved[i], S[i].note);
      if (sel && sel.page === i) sel = null;
      renderPage(i); updateHead(i); updateTop();
    }
    async function flush() {
      for (const i of new Set([...dirty, ...noteDirty])) persist(i);
      await Promise.all(Object.values(chains));
    }
    const busy = () => dirty.size > 0 || noteDirty.size > 0 || inflight > 0;

    // ---------------------------------------------------------------- undo (per page snapshots)
    function commit(i, mutate) {
      if (!allowEdit()) return false;
      const ps = S[i], before = layoutOf(ps);
      mutate(ps);
      pruneParts(ps);
      const after = layoutOf(ps);
      if (before !== after) { undoStack.push({page: i, text: before}); redoStack = []; changed(i); }
      return before !== after;
    }
    function step(from, to) {
      if (!from.length) { hint("Není co vrátit."); return; }
      if (!allowEdit()) return;
      const entry = from.pop();
      const ps = S[entry.page];
      to.push({page: entry.page, text: layoutOf(ps)});
      restore(ps, entry.text);
      if (sel && sel.page === entry.page && !regionOf(ps, sel.id)) sel = null;
      changed(entry.page);
      ensureVisible(entry.page);
      if (orderMode) updateOrderBar();
    }
    // In the order mode Ctrl+Z takes back the last click (Ctrl+Shift+Z puts it back); with no clicks left it undoes the layout.
    function undoRedo(redo) {
      const om = orderMode;
      if (om && !redo && om.clicks.length) { om.redo.push(om.clicks.pop()); updateOrderBar(); renderPage(om.page); return; }
      if (om && redo && om.redo.length) { om.clicks.push(om.redo.pop()); updateOrderBar(); renderPage(om.page); return; }
      if (om && redo && om.clicks.length) { hint("Nejdřív dokončete pořadí (Enter), pak lze znovu vrátit úpravy."); return; }
      redo ? step(redoStack, undoStack) : step(undoStack, redoStack);
    }
    function changed(i) {
      renderPage(i);
      updateHead(i);
      updateTop();
      scheduleSave(i);
    }

    // ---------------------------------------------------------------- order mode
    const viewOf = i => orderMode && orderMode.page === i ? applyOrder(S[i], orderMode.clicks) : S[i];
    function startOrder() {
      if (orderMode) { endOrder(true); return; }
      if (readOnly) { hint("Probíhá zpracování, rozvržení je teď jen ke čtení."); return; }
      closePanels();
      const page = sel ? sel.page : visiblePage();
      orderMode = {page, clicks: [], redo: []};
      sel = null;
      button("order").classList.add("on");
      updateOrderBar();
      renderAll();
    }
    function endOrder(save) {
      const om = orderMode;
      orderMode = null;
      button("order").classList.remove("on");
      $(".le-orderbar").hidden = true;
      if (save && om.clicks.length) {
        if (commit(om.page, ps => { const o = applyOrder(ps, om.clicks); ps.regions = o.regions; ps.parts = o.parts; ps.pnid = o.pnid; })) {
          hint(`Pořadí strany ${pageNo(om.page) + 1} uloženo (oklikáno ${om.clicks.length}).`);
        }
      }
      renderAll();
    }
    const cancelOrder = () => endOrder(false);
    function updateOrderBar() {
      const bar = $(".le-orderbar"), n = S[orderMode.page].regions.length;
      bar.hidden = false;
      bar.innerHTML = `<b>Pořadí strany ${pageNo(orderMode.page) + 1}</b><span>oklikáno ${orderMode.clicks.length} z ${n} · klik = další část · Shift+klik = další sloupec téže části · Backspace / Ctrl+Z = krok zpět, Ctrl+Shift+Z = znovu</span>
        <button type="button" data-act="done">Hotovo <kbd>Enter</kbd></button><button type="button" data-act="discard">Zahodit</button>`;
    }
    $(".le-orderbar").addEventListener("click", e => {
      const act = e.target.closest("button")?.dataset.act;
      if (act === "done") endOrder(true);
      if (act === "discard") { cancelOrder(); hint("Pořadí zahozeno."); }
    });
    function orderClick(i, e, px, py) {
      if (i !== orderMode.page) { hint(`Pořadí se teď zadává pro stranu ${pageNo(orderMode.page) + 1} (Enter = hotovo).`); return; }
      const h = hitTest(i, px, py, true);
      if (!h) return;
      if (orderMode.clicks.some(c => c.id === h.id)) { hint("Tento box už je v pořadí (Backspace = krok zpět)."); return; }
      orderMode.clicks.push({id: h.id, join: e.shiftKey && orderMode.clicks.length > 0});
      orderMode.redo = [];
      updateOrderBar();
      renderPage(i);
    }

    // ---------------------------------------------------------------- operations on the selected region
    function withSelected(fn) {
      const r = selectedRegion();
      if (!r) { hint("Nejdřív vyberte box."); return; }
      commit(sel.page, ps => fn(ps, regionOf(ps, sel.id)));
    }
    const setKind = kind => withSelected((ps, r) => { ps.parts[r.part].kind = kind; });
    const toggleLock = () => withSelected((ps, r) => { r.locked = !r.locked; });
    const rotate = stacked => withSelected((ps, r) => {
      r.orientation = stacked ? (r.orientation === "stacked" ? "0" : "stacked") : ROTATIONS[(ROTATIONS.indexOf(r.orientation) + 1) % 4];
    });
    function toggleContinues() {
      const r = selectedRegion();
      if (r && S[sel.page].regions[0].part === r.part) { hint("První část strany nemá po čem pokračovat."); return; }
      withSelected((ps, rr) => { const p = ps.parts[rr.part]; p.cont = !p.cont; });
    }
    function deleteSelected() {
      if (!selectedRegion()) return;
      const page = sel.page, id = sel.id;
      if (!commit(page, ps => { ps.regions = ps.regions.filter(r => r.id !== id); })) return;
      sel = null;
      renderPage(page);
    }
    // Alt+up/down: one place in the reading order. Crossing a part border moves a single-region part past the whole
    // neighbour part; a column of a multi-column part leaves its part and becomes a part of its own.
    function moveInOrder(dir) {
      const r = selectedRegion();
      if (!r) { hint("Nejdřív vyberte box."); return; }
      commit(sel.page, ps => {
        const list = ps.regions, i = indexOf(ps, sel.id), j = i + dir;
        if (j < 0 || j >= list.length) return;
        if (list[j].part === list[i].part) { [list[i], list[j]] = [list[j], list[i]]; return; }
        const neighbour = list[j].part;
        let own = list[i];
        if (list.filter(x => x.part === own.part).length > 1) {
          const pid = ps.pnid++;
          ps.parts[pid] = {kind: ps.parts[own.part].kind, title: "", cont: false};
          own = list[i] = {...own, part: pid};
          sel = {page: sel.page, id: own.id};
        }
        const rest = list.filter(x => x !== own);
        const group = rest.filter(x => x.part === neighbour);
        const at = dir > 0 ? rest.indexOf(group[group.length - 1]) + 1 : rest.indexOf(group[0]);
        rest.splice(at, 0, own);
        ps.regions = rest;
      });
      renderPage(sel.page);
    }
    function createRegion(i, boxValue) {
      let newId = null;
      const b = boxValue.map(r2);
      commit(i, ps => {
        const id = ps.nid++;
        const s = sel && sel.page === i ? regionOf(ps, sel.id) : null;
        if (s) {
          ps.regions.splice(indexOf(ps, s.id) + 1, 0, {id, part: s.part, box: b, orientation: s.orientation, locked: true});
        } else {
          const pid = ps.pnid++;
          ps.parts[pid] = {kind: "article", title: "", cont: false};
          const [, cy] = center(b);
          const above = ps.regions.filter(n => center(n.box)[1] < cy && Math.min(n.box[2], b[2]) - Math.max(n.box[0], b[0]) > 0);
          let at = ps.regions.length;
          if (above.length) {
            const nearest = above.reduce((m, n) => n.box[3] > m.box[3] ? n : m);
            at = ps.regions.map(x => x.part).lastIndexOf(nearest.part) + 1;
          }
          ps.regions.splice(at, 0, {id, part: pid, box: b, orientation: "0", locked: true});
        }
        newId = id;
      });
      if (newId == null) { renderPage(i); return; }
      sel = {page: i, id: newId};
      renderAll();
    }

    // ---------------------------------------------------------------- view: sizes and zoom
    const baseWidth = () => Math.max(300, Math.min(1000, scroller.clientWidth - 56));
    function layoutSizes() {
      const w = baseWidth() * zoom;
      for (const [i, g] of info.entries()) {
        const p = srv.pages[i];
        g.W = w; g.H = w * p.raster_height / p.raster_width;
        g.canvas.style.width = `${g.W}px`; g.canvas.style.height = `${g.H}px`;
      }
      button("zfit").textContent = `${Math.round(zoom * 100)} %`;
    }
    function zoomAt(newZoom, cx, cy) {
      newZoom = clamp(newZoom, 0.3, 8);
      if (newZoom === zoom || !info.length) return;
      let best = null, dist = Infinity;
      for (const g of info) {
        const r = g.canvas.getBoundingClientRect();
        const d = cy < r.top ? r.top - cy : cy > r.bottom ? cy - r.bottom : 0;
        if (d < dist) { dist = d; best = {g, fx: (cx - r.left) / r.width, fy: clamp((cy - r.top) / r.height, 0, 1)}; }
      }
      zoom = newZoom;
      store.set(ZOOM_KEY, String(Math.round(zoom * 1000) / 1000));
      layoutSizes();
      renderAll();
      const r = best.g.canvas.getBoundingClientRect();
      scroller.scrollLeft += r.left + best.fx * r.width - cx;
      scroller.scrollTop += r.top + best.fy * r.height - cy;
    }
    function zoomCentre(factor) {
      const r = scroller.getBoundingClientRect();
      zoomAt(zoom * factor, r.left + r.width / 2, r.top + r.height / 2);
    }
    function visiblePage() {
      const r = scroller.getBoundingClientRect(), mid = r.top + r.height / 2;
      let best = 0, bestD = Infinity;
      info.forEach((g, i) => {
        const c = g.canvas.getBoundingClientRect(), d = mid < c.top ? c.top - mid : mid > c.bottom ? mid - c.bottom : 0;
        if (d < bestD) { bestD = d; best = i; }
      });
      return best;
    }
    function ensureVisible(i) {
      const c = info[i].canvas.getBoundingClientRect(), r = scroller.getBoundingClientRect();
      if (c.bottom < r.top + 40 || c.top > r.bottom - 40) scroller.scrollTop += c.top - r.top - 40;
    }
    function scrollToGrid(i, rect) {
      const g = info[i], c = g.canvas.getBoundingClientRect(), r = scroller.getBoundingClientRect();
      const cx = c.left + (rect[0] + rect[2]) / 2 * g.W / GRID, cy = c.top + (rect[1] + rect[3]) / 2 * g.H / GRID;
      scroller.scrollTop += cy - (r.top + r.height / 2);
      scroller.scrollLeft += cx - (r.left + r.width / 2);
    }

    // ---------------------------------------------------------------- rendering
    function renderPage(i) {
      const g = info[i];
      if (!g) return;
      const v = viewOf(i), W = g.W, H = g.H, sx = W / GRID, sy = H / GRID;
      const lab = labels(v.regions), om = orderMode && orderMode.page === i ? orderMode : null;
      const clicked = om ? new Set(om.clicks.map(c => c.id)) : null;
      let out = `<defs><pattern id="hat${i}" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="4" height="9" fill="rgba(220,30,30,.6)"/></pattern>
        <pattern id="unk${i}" width="9" height="9" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><rect width="3" height="9" fill="rgba(60,60,60,.35)"/></pattern></defs>`;
      const px = b => [b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy];
      const selRegion = !om && sel && sel.page === i ? v.regions.find(r => r.id === sel.id) : null;

      for (const r of v.regions) {
        const [x0, y0, x1, y1] = px(r.box), hue = hueOf(lab[r.id].ordinal), part = v.parts[r.part];
        const unlocked = !r.locked, isSel = selRegion === r, isHover = hover && hover.page === i && hover.id === r.id && !om;
        const unknown = om && !clicked.has(r.id);
        const stroke = unlocked ? `hsl(${hue},100%,45%)` : `hsl(${hue},62%,38%)`;
        const fill = unlocked ? `hsla(${hue},95%,52%,.28)` : `hsla(${hue},70%,45%,${isHover ? .2 : .11})`;
        const tip = `${lab[r.id].label} · ${KIND_NAME[part.kind]}${part.title ? " · " + part.title : ""}${r.orientation !== "0" ? " · otočení " + r.orientation : ""}${unlocked ? " · odemčeno" : " · zamčeno"}`;
        if (isSel) out += `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="none" stroke="#fff" stroke-width="6" opacity=".9"/>`;
        out += `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="${fill}" stroke="${stroke}" stroke-width="${unlocked ? 2.6 : 1.5}"${unlocked ? ' stroke-dasharray="8 5"' : ""}><title>${esc(tip)}</title></rect>`;
        if (unknown) out += `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="url(#unk${i})" pointer-events="none"/>`;
      }
      // reading order arrows
      if (arrows) {
        for (let k = 0; k + 1 < v.regions.length; k++) {
          const a = v.regions[k], b = v.regions[k + 1];
          if (om && (!clicked.has(a.id) || !clicked.has(b.id)) && clicked.size) { if (!clicked.has(a.id)) continue; }
          const [ax, ay] = center(px(a.box)), [bx, by] = center(px(b.box));
          const same = a.part === b.part, hue = hueOf(lab[a.id].ordinal);
          const len = Math.hypot(bx - ax, by - ay);
          if (len < 14) continue;
          const ux = (bx - ax) / len, uy = (by - ay) / len, ex = bx - ux * 7, ey = by - uy * 7;
          const col = same ? `hsl(${hue},70%,32%)` : "#555";
          const cont = !same && v.parts[b.part].cont;
          out += `<g pointer-events="none" opacity=".85"><line x1="${ax}" y1="${ay}" x2="${ex}" y2="${ey}" stroke="${col}" stroke-width="${cont ? 3 : 1.8}"${same ? "" : ' stroke-dasharray="7 5"'}/>
            <polygon points="${bx - ux * 1},${by - uy * 1} ${ex - uy * 4.5},${ey + ux * 4.5} ${ex + uy * 4.5},${ey - ux * 4.5}" fill="${col}"/>
            <circle cx="${ax}" cy="${ay}" r="2.6" fill="${col}"/></g>`;
        }
      }
      // overlaps and ink spots
      for (const p of problems(i)) {
        const [x0, y0, x1, y1] = px(p.rect);
        if (p.type === "overlap") out += `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="url(#hat${i})" stroke="#d32020" stroke-width="1.5" pointer-events="none"><title>Překryv oblastí</title></rect>`;
        else out += `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="rgba(230,30,30,.18)" stroke="#d32020" stroke-width="2" stroke-dasharray="5 3"><title>Nepokrytý text na straně</title></rect>
          <text x="${x0 + 2}" y="${Math.max(11, y0 - 3)}" fill="#d32020">nepokryto</text>`;
      }
      // badges, tools, order overlay
      for (const r of v.regions) {
        const [x0, y0, x1, y1] = px(r.box), hue = hueOf(lab[r.id].ordinal), part = v.parts[r.part], isSel = selRegion === r;
        const first = v.regions.find(x => x.part === r.part) === r;
        const label = lab[r.id].label + (first && part.cont && lab[r.id].ordinal > 1 ? " ↪" : "");
        const bw = label.length * 7.4 + 10;
        out += `<g pointer-events="none"><rect x="${x0 + 3}" y="${y0 + 3}" width="${bw}" height="17" rx="5" fill="hsl(${hue},62%,34%)"/>
          <text x="${x0 + 8}" y="${y0 + 15.5}" fill="#fff">${esc(label)}</text>`;
        if (part.kind !== "article") {
          const name = KIND_NAME[part.kind], kw = name.length * 6.2 + 10;
          out += `<rect x="${x0 + 3 + bw + 3}" y="${y0 + 3}" width="${kw}" height="17" rx="5" fill="#2b2b2b"/><text x="${x0 + 3 + bw + 8}" y="${y0 + 15}" fill="#fff" style="font-size:11px;font-weight:500">${name}</text>`;
        }
        out += `</g>`;
        const t = om || readOnly ? null : tools(r, isSel, g);
        if (t) {
          for (const [key, icon] of [["lock", lockIcon(r.locked, t.lock.x, t.lock.y)], ["orient", orientIcon(r.orientation, t.orient.x, t.orient.y)]]) {
            const q = t[key];
            out += `<rect x="${q.x - 1}" y="${q.y - 1}" width="${q.s + 2}" height="${q.s + 2}" rx="4" fill="rgba(255,255,255,.92)" stroke="hsl(${hue},62%,34%)" stroke-width="1"><title>${key === "lock" ? (r.locked ? "Zamčeno – AI box nezmění (L)" : "Odemčeno – AI box smí upravit (L)") : "Orientace textu: " + (r.orientation === "stacked" ? "písmena pod sebou" : "otočit o " + r.orientation + "° po směru hodin, aby se četl (R)")}</title></rect>${icon}`;
          }
        }
        if (om) {
          const [cx, cy] = center([x0, y0, x1, y1]);
          out += clicked.has(r.id)
            ? `<text x="${cx}" y="${cy + 10}" text-anchor="middle" fill="hsl(${hue},62%,30%)" stroke="#fff" stroke-width="4" paint-order="stroke" style="font-size:30px;font-weight:700">${esc(lab[r.id].label)}</text>`
            : `<text x="${cx}" y="${cy + 10}" text-anchor="middle" fill="#444" stroke="#fff" stroke-width="4" paint-order="stroke" style="font-size:26px;font-weight:700">?</text>`;
        }
        if (isSel) {
          const hs = [[x0, y0], [(x0 + x1) / 2, y0], [x1, y0], [x1, (y0 + y1) / 2], [x1, y1], [(x0 + x1) / 2, y1], [x0, y1], [x0, (y0 + y1) / 2]];
          out += hs.map(([hx, hy]) => `<rect x="${hx - 4}" y="${hy - 4}" width="8" height="8" fill="#fff" stroke="#222" stroke-width="1.3" pointer-events="none"/>`).join("");
        }
      }
      if (ptr && ptr.type === "draw" && ptr.page === i && ptr.live) {
        const [x0, y0, x1, y1] = px(ptr.live);
        out += `<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" fill="rgba(8,127,140,.15)" stroke="#087f8c" stroke-width="2" stroke-dasharray="6 4" pointer-events="none"/>`;
      }
      if (flash && flash.page === i) {
        const [x0, y0, x1, y1] = px(flash.rect), pad = 6;
        out += `<rect class="flash" x="${x0 - pad}" y="${y0 - pad}" width="${x1 - x0 + 2 * pad}" height="${y1 - y0 + 2 * pad}" fill="none" stroke="#e53935" stroke-width="4" pointer-events="none"/>`;
      }
      g.svg.innerHTML = out;
    }
    function renderAll() { info.forEach((_, i) => renderPage(i)); }

    function updateHead(i) {
      const g = info[i], ps = S[i], edited = isEdited(i), unlocked = unlockedOf(ps).length, bad = problems(i).length;
      g.el.querySelector(".status").textContent = edited ? "upraveno" : "navrženo";
      g.el.querySelector(".status").classList.toggle("edited", edited);
      const warn = g.el.querySelector(".warn"), unl = g.el.querySelector(".unl"), note = g.el.querySelector(".notepill");
      warn.hidden = !bad; warn.textContent = `⚠ ${count(bad, PROBLEMS)}`;
      unl.hidden = !unlocked; unl.textContent = `odemčeno ${unlocked}`;
      note.hidden = !ps.note.trim();
      g.el.querySelector(".reset").hidden = !edited || readOnly;
    }
    // What the server says about the page below it: the note it was sent with, a round that could not redo the
    // page, an unusable AI answer.
    function renderNotices(i) {
      const p = srv.pages[i], parts = [];
      const kept = (srv.kept || []).find(k => k.index === p.index);
      if (kept) parts.push(`<div class="le-notice">Kolo rozvržení tuto stranu nemohlo přepracovat${kept.reason ? ` (${esc(kept.reason)})` : ""}. Zůstává tak, jak jste ji upravili, a při dalším zpracování se pošle znovu.</div>`);
      if (p.source === "failed") parts.push(`<div class="le-notice">AI k této straně nedala použitelnou odpověď${p.reason ? ` (${esc(p.reason)})` : ""}; strana je jedna oblast.</div>`);
      if (p.sent_note) parts.push(`<div class="le-sent">Poznámka odeslaná v kole ${p.round}: <q>${esc(p.sent_note)}</q></div>`);
      info[i].el.querySelector(".le-extra").innerHTML = parts.join("");
    }
    function updateTop() {
      const pages = srv.pages.length, edited = srv.pages.filter((_, i) => isEdited(i)).length;
      const bad = allProblems().length, unl = S.reduce((n, p) => n + unlockedOf(p).length, 0);
      $(".le-summary").innerHTML = `<span><b>${pages}</b> ${plural(pages, "strana", "strany", "stran")}</span><span>· upraveno <b>${edited}</b></span><span>· odemčeno <b>${unl}</b></span>` +
        (srv.round > 1 ? `<span>· kolo <b>${srv.round}</b></span>` : "") +
        (bad ? `<button type="button" class="le-next" title="Přeskočit na další problém (N)">⚠ ${count(bad, PROBLEMS)} <kbd>N</kbd></button>` : `<span>· bez problémů</span>`);
      const nb = $(".le-next");
      if (nb) nb.onclick = () => gotoProblem(1);
      const has = S.some(p => unlockedOf(p).length || p.note.trim());
      button("process").disabled = !has || readOnly;
      button("process").title = readOnly ? "Probíhá zpracování." : has ? "Co by se poslalo AI" : "Nic není odemčeno a žádná strana nemá poznámku.";
      const approve = button("approve"), approved = srv.state === "approved";
      approve.classList.toggle("done", approved);
      approve.textContent = approved ? "✓ Schváleno" : "Schválit";
      approve.disabled = readOnly || approved;
      button("order").disabled = readOnly;
      const status = $(".le-status");
      status.hidden = !(readOnly || approved);
      status.className = "le-status" + (readOnly ? " running" : "");
      status.textContent = readOnly ? "Probíhá zpracování (kolo rozvržení nebo další kroky). Rozvržení je teď jen ke čtení."
        : approved ? "Rozvržení je schváleno. Úprava zruší schválení a výsledky dalších kroků." : "";
      if (panel === "process") fillProcess();
    }

    function buildPages() {
      pagesEl.innerHTML = "";
      info = srv.pages.map((p, i) => {
        const el = document.createElement("section");
        el.className = "le-pg";
        el.innerHTML = `<div class="le-pg-head"><b>Strana ${p.index + 1}</b><span class="pill status"></span><span class="pill warn" hidden></span><span class="pill unl" hidden></span><span class="pill notepill" hidden>✎ poznámka</span>${p.round > 1 ? `<span class="pill">kolo ${p.round}</span>` : ""}<span class="sp"></span><button type="button" class="reset" hidden title="Vrátit návrh AI pro tuto stranu">↺ návrh AI</button></div>
          <div class="le-canvas" data-i="${i}"><img src="${esc(apiUrl(`/projects/${project.id}/pages/${p.index}/image?prepared=true`))}" alt="Strana ${p.index + 1}" draggable="false" decoding="async"><svg xmlns="${NS}"></svg></div>
          <textarea class="le-note" rows="1" placeholder="Poznámka ke straně pro AI…" spellcheck="false"></textarea>
          <div class="le-extra"></div>`;
        pagesEl.appendChild(el);
        const note = el.querySelector(".le-note");
        note.value = S[i].note;
        const grow = () => { note.style.height = "auto"; note.style.height = `${note.scrollHeight + 2}px`; note.classList.toggle("filled", !!note.value.trim()); };
        note.addEventListener("input", () => {
          if (note.value === S[i].note) return;
          if (!allowEdit()) { note.value = S[i].note; note.blur(); return; }
          S[i].note = note.value;
          grow(); scheduleNote(i); updateHead(i); updateTop();
        });
        note.addEventListener("focus", grow);
        note.addEventListener("blur", () => { if (noteDirty.has(i)) persist(i); });
        setTimeout(grow, 0);
        el.querySelector(".reset").addEventListener("click", () => {
          if (!confirm(`Vrátit rozvržení strany ${p.index + 1} na návrh AI? Tato úprava půjde vrátit klávesou Ctrl+Z.`)) return;
          // The proposal as an edit (undoable): every region locked again, the note stays.
          commit(i, ps => { Object.assign(ps, fromServer(p.proposal, ps.note, true)); });
          if (sel && sel.page === i) sel = null;
          renderAll();
        });
        const canvas = el.querySelector(".le-canvas");
        return {el, canvas, svg: canvas.querySelector("svg"), img: canvas.querySelector("img"), W: 0, H: 0};
      });
      layoutSizes();
      renderAll();
      info.forEach((_, i) => { updateHead(i); renderNotices(i); });
      updateTop();
    }

    // Take a server view as the state of the editor (the first time, and after a round or an approval).
    function load(data) {
      const left = scroller.scrollLeft, top = scroller.scrollTop;
      if (orderMode) { orderMode = null; button("order").classList.remove("on"); $(".le-orderbar").hidden = true; }
      srv = data;
      S = srv.pages.map(p => fromServer(p.parts, p.note || ""));
      saved = srv.pages.map(p => p.parts);
      proposalSig = srv.pages.map(p => signature(fromServer(p.proposal, "", true)));
      sel = null; hover = null; flash = null; ptr = null; undoStack = []; redoStack = []; problemCursor = null;
      approvalAccepted = false;
      closePanels();
      buildPages();
      scroller.scrollLeft = left; scroller.scrollTop = top;
    }

    // ---------------------------------------------------------------- hit testing (page pixel coordinates)
    function hitTest(i, px, py, bodyOnly = false) {
      const g = info[i], v = viewOf(i), sx = g.W / GRID, sy = g.H / GRID;
      const selId = sel && sel.page === i ? sel.id : null;
      const boxes = v.regions.map(r => ({r, x0: r.box[0] * sx, y0: r.box[1] * sy, x1: r.box[2] * sx, y1: r.box[3] * sy}));
      if (!bodyOnly) {
        for (const b of [...boxes].reverse()) {
          const t = tools(b.r, b.r.id === selId, g);
          if (!t) continue;
          if (within(t.lock, px, py)) return {type: "lock", id: b.r.id};
          if (within(t.orient, px, py)) return {type: "orient", id: b.r.id};
        }
        let best = null;
        for (const b of boxes) {
          const T = Math.max(2, Math.min(EDGE, (b.x1 - b.x0) / 3, (b.y1 - b.y0) / 3));
          if (px < b.x0 - T || px > b.x1 + T || py < b.y0 - T || py > b.y1 + T) continue;
          const dl = Math.abs(px - b.x0), dr = Math.abs(px - b.x1), dt = Math.abs(py - b.y0), db = Math.abs(py - b.y1);
          const ex = dl <= T && dl <= dr ? "min" : dr <= T ? "max" : null, ey = dt <= T && dt <= db ? "min" : db <= T ? "max" : null;
          if (!ex && !ey) continue;
          const dist = Math.min(ex ? (ex === "min" ? dl : dr) : Infinity, ey ? (ey === "min" ? dt : db) : Infinity);
          const area = (b.x1 - b.x0) * (b.y1 - b.y0), isSel = b.r.id === selId;
          if (!best || dist < best.dist - 0.5 || (Math.abs(dist - best.dist) <= 0.5 && ((isSel && !best.isSel) || (isSel === best.isSel && area < best.area)))) {
            best = {type: "resize", id: b.r.id, edges: {x: ex, y: ey}, dist, area, isSel};
          }
        }
        if (best) return best;
      }
      let inside = null;
      for (const b of boxes) {
        const pad = bodyOnly ? 2 : 0;
        if (px < b.x0 - pad || px > b.x1 + pad || py < b.y0 - pad || py > b.y1 + pad) continue;
        const area = (b.x1 - b.x0) * (b.y1 - b.y0);
        if (!inside || area < inside.area || (area === inside.area && b.r.id === selId)) inside = {type: "move", id: b.r.id, area};
      }
      return inside;
    }

    // ---------------------------------------------------------------- pointer interaction
    function local(e, i) {
      const g = info[i], r = g.canvas.getBoundingClientRect();
      const px = e.clientX - r.left, py = e.clientY - r.top;
      return {px, py, gx: px / r.width * GRID, gy: py / r.height * GRID};
    }
    scroller.addEventListener("mousedown", e => { if (e.button === 1) e.preventDefault(); });
    scroller.addEventListener("pointerdown", e => {
      if (document.activeElement && document.activeElement !== document.body && !e.target.closest("textarea")) document.activeElement.blur();
      if (e.button === 1 || (e.button === 0 && space)) {
        ptr = {type: "pan", x: e.clientX, y: e.clientY, left: scroller.scrollLeft, top: scroller.scrollTop};
        scroller.setPointerCapture(e.pointerId);
        scroller.style.cursor = "grabbing";
        e.preventDefault();
        return;
      }
      if (e.button !== 0) return;
      const canvas = e.target.closest(".le-canvas");
      if (!canvas || readOnly) return;
      const i = +canvas.dataset.i, {px, py, gx, gy} = local(e, i);
      if (orderMode) { orderClick(i, e, px, py); e.preventDefault(); return; }
      const h = hitTest(i, px, py), ps = S[i];
      scroller.setPointerCapture(e.pointerId);
      if (h && (h.type === "lock" || h.type === "orient")) {
        sel = {page: i, id: h.id};
        if (h.type === "lock") toggleLock(); else rotate(e.shiftKey);
        renderAll();
        ptr = null;
        return;
      }
      if (!h) { ptr = {type: "draw", page: i, sx: e.clientX, sy: e.clientY, start: [gx, gy], live: null, started: false}; return; }
      const prev = sel;
      sel = {page: i, id: h.id};
      if (prev && prev.page !== i) renderPage(prev.page);
      ptr = {type: h.type, page: i, id: h.id, edges: h.edges, sx: e.clientX, sy: e.clientY, start: [gx, gy], moved: false,
        before: layoutOf(ps), orig: Object.fromEntries(ps.regions.map(r => [r.id, r.box.slice()]))};
      renderPage(i);
    });
    scroller.addEventListener("pointermove", e => {
      if (ptr) {
        if (ptr.type === "pan") {
          scroller.scrollLeft = ptr.left - (e.clientX - ptr.x);
          scroller.scrollTop = ptr.top - (e.clientY - ptr.y);
          return;
        }
        const i = ptr.page, {gx, gy} = local(e, i), ps = S[i];
        if (ptr.type === "draw") {
          if (!ptr.started && Math.hypot(e.clientX - ptr.sx, e.clientY - ptr.sy) < SLOP) return;
          ptr.started = true;
          const [ax, ay] = ptr.start;
          ptr.live = [clamp(Math.min(ax, gx), 0, GRID), clamp(Math.min(ay, gy), 0, GRID), clamp(Math.max(ax, gx), 0, GRID), clamp(Math.max(ay, gy), 0, GRID)];
          renderPage(i);
          return;
        }
        if (!ptr.moved && Math.hypot(e.clientX - ptr.sx, e.clientY - ptr.sy) < SLOP) return;
        ptr.moved = true;
        const dx = gx - ptr.start[0], dy = gy - ptr.start[1];
        const res = ptr.type === "move" ? moveBoxes(ptr.orig, ptr.id, dx, dy, e.shiftKey)
          : resizeBoxes(ptr.orig, ptr.id, ptr.edges, dx, dy, e.shiftKey);
        if (!res) return;                       // a pushed neighbour would leave the page: keep the last position
        for (const r of ps.regions) r.box = res[r.id].map((v, k) => v === ptr.orig[r.id][k] ? v : r2(v));
        renderPage(i);
        return;
      }
      const canvas = e.target.closest?.(".le-canvas");
      let cursor = space ? "grab" : "";
      let nextHover = null;
      if (canvas && !space && !readOnly) {
        const i = +canvas.dataset.i, {px, py} = local(e, i), h = orderMode ? hitTest(i, px, py, true) : hitTest(i, px, py);
        cursor = orderMode ? (h ? "pointer" : "default") : cursorFor(h);
        if (h && h.id != null && !orderMode) nextHover = {page: i, id: h.id};
      }
      scroller.style.cursor = cursor;
      if ((hover?.id) !== (nextHover?.id) || hover?.page !== nextHover?.page) {
        const old = hover;
        hover = nextHover;
        if (old) renderPage(old.page);
        if (hover) renderPage(hover.page);
      }
    });
    const endPointer = () => {
      if (!ptr) return;
      const p = ptr;
      ptr = null;
      scroller.style.cursor = space ? "grab" : "";
      if (p.type === "pan") return;
      const i = p.page, ps = S[i];
      if (p.type === "draw") {
        if (p.started && p.live && p.live[2] - p.live[0] >= MIN && p.live[3] - p.live[1] >= MIN) createRegion(i, p.live);
        else { if (!p.started) { sel = null; renderAll(); } else renderPage(i); }
        return;
      }
      if (p.moved && layoutOf(ps) !== p.before) {
        if (!allowEdit()) { restore(ps, p.before); renderPage(i); return; }
        undoStack.push({page: i, text: p.before});
        redoStack = [];
        changed(i);
      } else renderPage(i);
    };
    scroller.addEventListener("pointerup", endPointer);
    scroller.addEventListener("pointercancel", endPointer);
    scroller.addEventListener("pointerleave", () => { if (!ptr && hover) { const old = hover; hover = null; renderPage(old.page); } });
    scroller.addEventListener("wheel", e => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      zoomAt(zoom * Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0016)), e.clientX, e.clientY);
    }, {passive: false});
    scroller.addEventListener("dragstart", e => e.preventDefault());
    scroller.addEventListener("contextmenu", e => { if (ptr) e.preventDefault(); });

    // ---------------------------------------------------------------- problems
    function gotoProblem(dir) {
      const list = allProblems();
      if (!list.length) { hint("Žádné problémy."); return; }
      const key = p => p.page * 10000 + p.rect[1] * 5 + p.rect[0] / 1000;
      let next;
      if (!problemCursor) {
        const vp = visiblePage();
        next = dir > 0 ? list.find(p => p.page >= vp) || list[0] : [...list].reverse().find(p => p.page <= vp) || list[list.length - 1];
      } else {
        const c = problemCursor;
        next = dir > 0 ? list.find(p => key(p) > key(c) + 1e-6) || list[0] : [...list].reverse().find(p => key(p) < key(c) - 1e-6) || list[list.length - 1];
      }
      problemCursor = next;
      const index = list.indexOf(next) + 1;
      scrollToGrid(next.page, next.rect);
      flash = {page: next.page, rect: next.rect};
      renderPage(next.page);
      hint(`Problém ${index} z ${list.length}: ${next.type === "overlap" ? "překryv oblastí" : "nepokrytý text"} (strana ${pageNo(next.page) + 1}).`);
      setTimeout(() => { if (!disposed && flash && flash.rect === next.rect) { flash = null; renderPage(next.page); } }, 1500);
    }

    // ---------------------------------------------------------------- panels and the round
    function closePanels() { panel = null; $(".le-process").hidden = true; $(".le-help").hidden = true; }
    function fillProcess() {
      const el = $(".le-process");
      const rows = [];
      let pages = 0, boxes = 0;
      S.forEach((ps, i) => {
        const lab = labels(ps.regions), unl = unlockedOf(ps), note = ps.note.trim();
        if (!unl.length && !note) return;
        pages++; boxes += unl.length;
        const locked = ps.regions.length - unl.length;
        const names = unl.map(r => lab[r.id].label).join(", ");
        rows.push(`<div class="row" data-page="${i}"><b>Strana ${pageNo(i) + 1}</b> — ${count(locked, ["zamčený box zůstane", "zamčené boxy zůstanou", "zamčených boxů zůstane"])}` +
          (unl.length ? `, k přepracování ${count(unl.length, UNLOCKED)} (${esc(names)})` : "") +
          (!unl.length ? `<div class="hint">nic není odemčeno, AI může jen přidat</div>` : "") +
          (note ? `<q>${esc(note.length > 160 ? note.slice(0, 160) + "…" : note)}</q>` : "") + `</div>`);
      });
      el.innerHTML = `<h3>Zpracovat — co se pošle AI</h3>` +
        (rows.length ? `<div class="muted">${count(pages, ["strana", "strany", "stran"])}, ${count(boxes, UNLOCKED)}. Zamčené boxy AI nezmění, odemčené smí přepracovat. Ostatní strany se znovu neposílají.</div>${rows.join("")}
            <div class="le-panel-actions"><button type="button" class="primary" data-act="send">Zpracovat</button><button type="button" data-act="cancel">Zavřít</button></div>`
          : `<p class="muted">Nic není odemčeno a žádná strana nemá poznámku.</p>`);
      el.querySelectorAll(".row").forEach(row => row.addEventListener("click", () => {
        const i = +row.dataset.page;
        scroller.scrollTop += info[i].el.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 12;
      }));
      const send = el.querySelector('[data-act="send"]');
      if (send) send.onclick = () => runRound();
      const cancel = el.querySelector('[data-act="cancel"]');
      if (cancel) cancel.onclick = closePanels;
    }
    function togglePanel(name) {
      if (panel === name) { closePanels(); return; }
      closePanels();
      panel = name;
      $(name === "process" ? ".le-process" : ".le-help").hidden = false;
      if (name === "process") fillProcess();
    }
    async function runRound() {
      try {
        await flush();
        await api(`${base}/process`, "POST");
        closePanels();
        toast("Kolo rozvržení se spouští: AI přepracuje odeslané strany.");
        await window.ocrmaker.refresh();
      } catch (e) { toast(e.message); }
    }
    async function approve() {
      if (readOnly || srv.state === "approved") return;
      const bad = allProblems().length, unl = S.reduce((n, p) => n + unlockedOf(p).length, 0);
      const warnings = [bad && `zůstává ${count(bad, PROBLEMS)}`, unl && `${count(unl, UNLOCKED)} se nepřepracuje`].filter(Boolean);
      if (warnings.length && !confirm(`Schválit rozvržení celého dokumentu? Pozor: ${warnings.join(", ")}.\n\nSchválením zpracování pokračuje dalšími kroky.`)) return;
      try {
        await flush();
        await api(`${base}/approve`, "POST");
        toast("Rozvržení je schváleno, zpracování pokračuje.");
        await window.ocrmaker.refresh();
      } catch (e) { toast(e.message); }
    }

    // ---------------------------------------------------------------- keys and buttons
    const editing = t => t && t.matches && t.matches("textarea, input, select, [contenteditable]");
    function onKeydown(e) {
      if (!isActive()) return;
      const t = e.target;
      if (editing(t)) { if (e.key === "Escape") t.blur(); return; }
      if (document.querySelector("dialog[open]")) return;
      if (t.closest && t.closest("button") && (e.code === "Space" || e.key === "Enter")) return;   // the button's own key
      if (e.code === "Space") { e.preventDefault(); if (!space) { space = true; if (!ptr) scroller.style.cursor = "grab"; } return; }
      const key = e.key.toLowerCase();
      if (e.ctrlKey || e.metaKey) {
        if (key === "z") { e.preventDefault(); undoRedo(e.shiftKey); }
        else if (key === "y") { e.preventDefault(); undoRedo(true); }
        return;
      }
      if (orderMode) {
        if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); endOrder(true); }
        else if (e.key === "Backspace") { e.preventDefault(); const c = orderMode.clicks.pop(); if (c) orderMode.redo.push(c); updateOrderBar(); renderPage(orderMode.page); }
        else if (key === "o") endOrder(true);
        return;
      }
      const digit = /^(?:Digit|Numpad)([1-4])$/.exec(e.code);
      if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); moveInOrder(e.key === "ArrowUp" ? -1 : 1); return; }
      if (e.altKey) return;
      if (digit && !e.shiftKey) { setKind(KIND_KEYS[+digit[1] - 1]); return; }
      if (e.key === "Escape") { if (panel) closePanels(); else if (sel) { sel = null; renderAll(); } return; }
      if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); deleteSelected(); return; }
      if (e.key === "?") { togglePanel("help"); return; }
      if (e.key === "+" || e.key === "=") { zoomCentre(1.25); return; }
      if (e.key === "-") { zoomCentre(0.8); return; }
      switch (key) {
        case "l": toggleLock(); break;
        case "r": rotate(e.shiftKey); break;
        case "o": startOrder(); break;
        case "j": toggleContinues(); break;
        case "n": gotoProblem(e.shiftKey ? -1 : 1); break;
        case "a": arrows = !arrows; renderAll(); break;
        default:
      }
    }
    const onKeyup = e => { if (e.code === "Space") { space = false; if (!ptr) scroller.style.cursor = ""; } };
    const onBlur = () => { space = false; };
    const onDocPointer = e => { if (panel && !e.target.closest(".le-panel, [data-act=process], [data-act=help]")) closePanels(); };
    const onResize = () => { if (isActive()) { layoutSizes(); renderAll(); } };
    const onPageHide = () => { flush(); };
    window.addEventListener("keydown", onKeydown);
    window.addEventListener("keyup", onKeyup);
    window.addEventListener("blur", onBlur);
    window.addEventListener("resize", onResize);
    window.addEventListener("pagehide", onPageHide);
    document.addEventListener("pointerdown", onDocPointer, true);

    button("order").onclick = startOrder;
    button("process").onclick = () => togglePanel("process");
    button("help").onclick = () => togglePanel("help");
    button("zin").onclick = () => zoomCentre(1.25);
    button("zout").onclick = () => zoomCentre(0.8);
    button("zfit").onclick = () => { zoom = 1; store.set(ZOOM_KEY, "1"); layoutSizes(); renderAll(); };
    button("approve").onclick = () => approve();

    // ---------------------------------------------------------------- following the server
    // Called by app.js with every poll of the project listing. A finished round or approval (running went false)
    // reloads the pages; a state change that did not come from this editor refreshes the status only.
    async function refreshFromServer(full) {
      reloading = true;
      try {
        const data = await api(base);
        if (disposed) return;
        const same = data.pages.length === srv.pages.length && data.round === srv.round
          && data.pages.every((p, i) => p.round === srv.pages[i].round);
        if (full || !same) load(data);
        else {
          Object.assign(srv, {state: data.state, discards: data.discards, kept: data.kept, running: data.running});
          data.pages.forEach((p, i) => applyMeta(i, p));
          info.forEach((_, i) => renderNotices(i));
          updateTop();
        }
      } catch { /* no layout any more (the step was rerun): app.js re-renders the step */ } finally { reloading = false; }
    }
    function update(current) {
      if (disposed) return;
      const running = !!current.running;
      if (running !== readOnly) {
        readOnly = running;
        if (running) { if (orderMode) cancelOrder(); ptr = null; }
        renderAll();
        info.forEach((_, i) => updateHead(i));
        updateTop();
      }
      const ended = wasRunning && !running;
      wasRunning = running;
      if (running || busy() || reloading) return;
      if (ended || (current.layout && current.layout !== srv.state)) refreshFromServer(ended);
    }
    function dispose() {
      if (disposed) return;
      flush();
      disposed = true;
      for (const t of [...Object.values(timers), ...Object.values(noteTimers), toastTimer]) clearTimeout(t);
      window.removeEventListener("keydown", onKeydown);
      window.removeEventListener("keyup", onKeyup);
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("resize", onResize);
      window.removeEventListener("pagehide", onPageHide);
      document.removeEventListener("pointerdown", onDocPointer, true);
    }

    load(srv);
    root.scrollIntoView({block: "nearest"});   // the editor is tall: bring it into view
    window.ocrmaker.livePreview({update, dispose});
    return true;
  }

  window.stepPreviews.regions = async (container, project, step) => {
    try {
      if (await mount(container, project)) return;
    } catch (e) {
      if (e.status !== 422) throw e;   // no layout regions (the blocks method, or Layout not run): the overlay below
    }
    await legacy(container, project, step);
  };
})();
