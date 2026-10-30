"use strict";
// Export step preview: the exported files and the run report to download, and the written text layer word by
// word, with the words that export verification found a problem with highlighted.
(function () {
  const {pageViewer, box, api, apiUrl, esc} = window.ocrmaker;
  const size = bytes => bytes >= 1048576 ? `${(bytes / 1048576).toLocaleString("cs-CZ", {maximumFractionDigits: 1})} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} kB`;
  // Czech names of the verification finding kinds (ocrmaker.verify.KINDS).
  const KINDS = {order: "jiné pořadí, než bylo plánováno", outside_box: "leží mimo rámeček slova",
                 text: "kopíruje se jinak", missing: "v PDF chybí", extra: "text navíc"};

  function finding(item) {
    return KINDS[item.kind] + (item.kind === "text" && item.extracted != null ? `: „${item.extracted}“` : "");
  }

  function draw(layer, overlay) {
    overlay.words.forEach((word, position) => {
      const flagged = word.findings && word.findings.length;
      const tip = `„${word.text}“\n${position + 1}. slovo textové vrstvy · ${word.id}`
        + (flagged ? `\nKontrola: ${word.findings.map(finding).join("; ")}` : "");
      box(word.bbox, {class: flagged ? "export-word export-finding" : "export-word", "data-tip": tip}, layer);
    });
    for (const extra of overlay.extra || []) {
      box(extra.bbox, {class: "export-word export-finding", "data-tip": `„${extra.text}“\nKontrola: ${KINDS.extra}`}, layer);
    }
  }

  function describe(overlay, page) {
    if (!overlay) return "";
    const flagged = overlay.words.filter(word => word.findings && word.findings.length).length
      + (overlay.extra || []).length;
    return `Strana ${page.index + 1}: ${overlay.words.length} slov v textové vrstvě`
      + (flagged ? `, kontrola našla problém u ${flagged}` : ", kontrola bez nálezů");
  }

  window.stepPreviews.export = async (container, project, step) => {
    const outputs = (await api(`/projects/${project.id}/outputs`)).outputs;
    const reports = (await api(`/projects/${project.id}/reports`)).reports;
    const links = outputs.map(output => {
      const url = apiUrl(`/projects/${project.id}/outputs/${encodeURIComponent(output.name)}`);
      return `<a class="output-link" href="${esc(url)}" download="${esc(output.name)}">⬇ ${esc(output.name)} <span>${esc(size(output.size))}</span></a>`;
    });
    if (reports.length) {
      links.push(`<a class="output-link" href="${esc(apiUrl(`/projects/${project.id}/report/summary`))}" target="_blank" rel="noopener">Zpráva o běhu <span>shrnutí</span></a>`,
                 `<a class="output-link" href="${esc(apiUrl(`/projects/${project.id}/report`))}" target="_blank" rel="noopener">Zpráva o běhu <span>JSON</span></a>`);
    }
    container.innerHTML = `<div class="outputs">${outputs.length ? links.join("")
      : `<p class="placeholder">Zatím nebylo nic exportováno.</p>`}</div><div class="export-viewer"></div>`;
    await pageViewer(container.querySelector(".export-viewer"), project, step, {
      draw, describe,
      legend: [{label: "slovo v textové vrstvě", cls: "export-word"},
               {label: "slovo s nálezem kontroly", cls: "export-finding"}],
    });
  };
})();
