"use strict";
// Export step preview: the exported files to download, and the written text layer word by word.
(function () {
  const {pageViewer, box, api, apiUrl, esc} = window.ocrmaker;
  const size = bytes => bytes >= 1048576 ? `${(bytes / 1048576).toLocaleString("cs-CZ", {maximumFractionDigits: 1})} MB`
    : `${Math.max(1, Math.round(bytes / 1024))} kB`;

  function draw(layer, overlay) {
    overlay.words.forEach((word, position) => {
      box(word.bbox, {class: "export-word", "data-tip": `„${word.text}“\n${position + 1}. slovo textové vrstvy · ${word.id}`}, layer);
    });
  }

  window.stepPreviews.export = async (container, project, step) => {
    const outputs = (await api(`/projects/${project.id}/outputs`)).outputs;
    container.innerHTML = `<div class="outputs">${outputs.length ? outputs.map(output => {
      const url = apiUrl(`/projects/${project.id}/outputs/${encodeURIComponent(output.name)}`);
      return `<a class="output-link" href="${esc(url)}" download="${esc(output.name)}">⬇ ${esc(output.name)} <span>${esc(size(output.size))}</span></a>`;
    }).join("") : `<p class="placeholder">Zatím nebylo nic exportováno.</p>`}</div><div class="export-viewer"></div>`;
    await pageViewer(container.querySelector(".export-viewer"), project, step, {
      draw,
      describe: (overlay, page) => overlay ? `Strana ${page.index + 1}: ${overlay.words.length} slov v textové vrstvě` : "",
      legend: [{label: "slovo v textové vrstvě", cls: "export-word"}],
    });
  };
})();
