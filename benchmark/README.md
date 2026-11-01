# Benchmark

Measures the automatic workflow (everything before manual review) against reference transcriptions.
Code: `src/ocrmaker/benchmark.py` (trials, command line) and `src/ocrmaker/evaluation.py` (scoring).

- `corrections/<document>.json`: findings about a reference transcription itself, applied before scoring
  (`[{"page": 1, "bbox": [x0, y0, x1, y1], "text": "…", "note": "why"}]`; empty text removes the word).
  The source PDF is the authority: add a correction only after looking at the scan.
- `JOURNAL.md`: what each optimisation round tried, its numbers and what was kept.

Documents, trial projects and trial records live outside the repository, in `<data dir>/benchmark`
(`~/.local/share/ai-ocr-maker/benchmark`). Open its projects in the app with
`./start.sh --data-dir ~/.local/share/ai-ocr-maker/benchmark`.

## Commands

    .venv/bin/python -m ocrmaker.benchmark add NAME SOURCE.pdf REFERENCE.pdf
    .venv/bin/python -m ocrmaker.benchmark run --provider claude --model haiku --effort high [--set KEY=JSON] [--label L]
    .venv/bin/python -m ocrmaker.benchmark list
    .venv/bin/python -m ocrmaker.benchmark show TRIAL [--kind silent] [--page 2] [--limit 40]
    .venv/bin/python -m ocrmaker.benchmark crops TRIAL --out DIR [--kind …] [--page …]
    .venv/bin/python -m ocrmaker.benchmark rescore TRIAL…

A trial copies the document's base (Import, Image preparation, Local OCR and Layout, kept per code version),
runs the remaining automatic steps with the configuration, exports without manual corrections and scores
the export. Changing the local steps' code rebuilds the base on the next trial (a minute or two); changes to
AI steps or scoring reuse it. Compare trials only when their `corrections` digest is the same (`rescore`).

## Reading the scores

Words are paired by their boxes; a box need not match to the pixel (see `evaluation.py`). The goal for a box
is that it wraps its printed word (or lone character) snugly, so selecting text in the PDF feels natural.
The reference's boxes are evidence, not the standard: a `box`, `split` or `merged` finding is a candidate to
judge on the crop, and ours may well be the better box.

| Column | Meaning | Target |
|---|---|---|
| correct % | reference words whose text the result has exactly | up |
| failed | the step that stopped the run; the scores of a failed trial mean nothing | empty |
| silent | wrong or missing text manual review does not list: nobody will see it | 0 |
| geometry | extra boxes, split or merged words, boxes far off | 0 |
| order | runs of text out of the reference reading order / words in them | 0 |
| review | review queue items (illegible markers and doubtful readings) | only what cannot be determined |
| unnecessary | review items whose OCR text was already right (`flagged` findings are review items the reference reads: judge them on the scan) | 0 |
| ai s, tokens | AI time and tokens of the trial | down, last priority |

`crops` draws the reference box green and ours red. A finding can be the reference's mistake: judge it on
the scan and record a correction, then `rescore`.
