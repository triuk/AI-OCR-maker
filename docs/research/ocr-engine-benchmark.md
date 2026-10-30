# OCR engine benchmark (throwaway, one sample)

Sample: `zpravodaj-2000-05-c03.pdf`, 6 pages, 200 DPI grayscale scans, Czech newsletter (multi-column, gutter blur on the left edge).
Scratchpad root (all scripts, outputs, overlays): `/tmp/claude-1000/-home-gordon-Dokumenty-drop-analysis/a576cbb2-e149-463f-ac7b-f09ccaf65516/scratchpad/ocr-bench/`

## Method

- Pages rasterised with `pdftoppm -r 200` (1589x2356). All engines run on the same PNGs.
- Hand-made ground truth (read visually from crops, `gt/A.txt B.txt C.txt`, 126/160/153 words):
  - **A** clean body text (p3, right column).
  - **B** gutter-blurred list (p3, left column, first letters smeared).
  - **C** heading + small print + two columns (p1, left edge is cut/blurred, so some GT words are inferred from context).
- Region metrics, "geom" mode: engine words inside the region sorted by geometry (isolates recognition from reading order). Words are compared in order (CER on joined text, WER by word edit distance).
- "page-order" mode: the engine's whole-page text in its own order is aligned (semi-global) with each GT passage; this also tests reading order.
- Box tightness: IoU of the word box with the ink bounding box inside it (adaptive threshold).
- Caveat: GT is only about 440 words by one reader. Differences below about 1 percentage point of CER are noise.

## Versions

| Component | Version |
|---|---|
| Python | 3.12.16 (uv, standalone; system python is 3.14 which has no wheels) |
| Tesseract | libtesseract 5.5.3 via `tesserocr==2.12.0` manylinux wheel (bundles the libs, no sudo), `ces.traineddata` from tessdata_best (10.9 MB) and tessdata_fast (3.8 MB), LSTM only |
| Surya | `surya-ocr==0.23.0` = Surya 2.5 (VLM `datalab-to/surya-ocr-2.5`, ~690M params) + separate torch text-line detector; served through `vllm==0.25.1` (torch 2.15, cu13 driver OK) |
| PaddleOCR | `paddleocr==3.7.0`, `paddlex==3.7.2`, `paddlepaddle-gpu==3.3.1` (cu126 index; the PyPI 2.6.2 build does not work with paddleocr 3.x), `lang=cs` maps to `latin_PP-OCRv5_mobile_rec` + `PP-OCRv5_server_det`; also PP-OCRv6 medium; PP-StructureV3 (`PP-DocLayout_plus-L`) |
| docTR | `python-doctr==1.1.0`, torch 2.15, `db_resnet50` + `parseq` |

## Results

Recognition (geom mode, lower is better; CER_nd = diacritics stripped before comparison):

| Engine | A CER | B CER | C CER | C WER | diacritic-word acc A/B/C |
|---|---|---|---|---|---|
| Tesseract 5 `ces` best | 0.3% | 0.1% | 3.8% | 9.8% | 97 / 100 / 92% |
| Tesseract fast | 0.5% | 0.3% | 4.1% | 11.1% | 96 / 100 / 91% |
| Surya det lines + Tesseract per line | 1.3% | 0.2% | 2.5% | 7.2% | 97 / 100 / 91% |
| PaddleOCR PP-OCRv5 latin (+ my word split) | 1.5% | 0.1% | 1.6% | 5.2% | 93 / 99 / 92% |
| PaddleOCR PP-OCRv6 | 1.5% | 0.2% | 1.7% | 4.6% | 93 / 98 / 92% |
| docTR db_resnet50+parseq | 13.2% (3.1% nd) | 14.8% (1.6% nd) | 14.0% (5.3% nd) | 59% | 13 / 1 / 2% |
| Surya 2.5 full-page OCR (page-order mode, block level, no boxes) | 0.3% | 0.2% | 1.2% | | |

Remaining Tesseract and Paddle errors are almost all the words cut by the gutter in region C (`službou`, `spojení`, `června` ...) plus Slovak `ô` (`Dôvera`), which the Czech Tesseract model cannot output (the PaddleOCR latin model gets it).

Geometry, speed, resources (6-page averages, RTX 4070 Laptop; 31 GB RAM, 28 threads):

| Engine | Word boxes | Box IoU vs ink (A/B/C) | Char boxes | Confidence | s/page GPU | s/page CPU | Peak VRAM |
|---|---|---|---|---|---|---|---|
| Tesseract | real words, tight | 0.94 / 0.96 / 0.96 | yes (`RIL.SYMBOL`, ~5k per page) | per word, separates right from wrong (0.95 vs 0.44-0.6) | n/a | 6.2 (4.8 on 1 thread) | none; RAM ~200 MB |
| Surya detector | text lines only, very clean | n/a | no | line score | 0.65 | not measured | small |
| Surya 2.5 VLM | block level (HTML text) only, no word or char boxes | n/a | no | mean token prob at best | 23 full page, 4 layout only (via vLLM) | not possible without llama-server | ~5.6 GB at vLLM 0.65 util |
| PaddleOCR | own "word" boxes are broken for accented Latin (split at diacritics); lines are good; rebuilt words by interpolation | 0.72-0.75 | no | per line only (0.96 even on smeared words) | 0.84 (v6 1.3) | ~55 | 3.7-5.8 GB peak incl. allocator pool |
| PP-StructureV3 | layout blocks + lines | n/a | no | line | 1.1 | CPU run crashed (oneDNN/PIR error) | 6.2 GB |
| docTR | word boxes, padded | 0.63-0.67 | no | per word, uninformative (0.97 wrong) | 1.6 | 13-19 | 0.57 GB |

Install footprint: Tesseract wheel 5.5 MB + 11 MB model (no sudo). docTR venv ~6 GB (torch+CUDA), models 190 MB. PaddleOCR venv ~5-7 GB, models 470 MB. Surya venv ~5.6 GB, VLM weights 1.3 GB, detector 74 MB, plus vLLM venv 9 GB.

Licences: Tesseract Apache-2.0, tesserocr MIT, tessdata Apache-2.0. PaddleOCR/Paddle Apache-2.0. docTR Apache-2.0. Surya code Apache-2.0 but **model weights are modified AI Pubs Open RAIL-M: free for research, personal use and startups under 5M funding/revenue, otherwise paid licence from Datalab** (check this against the project's intended use). No GPL component anywhere.

## Observations

**Gutter / blurred words** (page 5 left column, 17 heavily smeared line starts; overlays and numbers from `scripts` output, raw crop `/tmp/claude-1000/-home-gordon-Dokumenty-drop-analysis/a576cbb2-e149-463f-ac7b-f09ccaf65516/scratchpad/ocr-bench/regions/gutter_p5_raw.png`):
- Tesseract always emits a box at the true line start (x~28) with low confidence (0.0-0.4) and partial text (`Wuntují` for "garantují"). This is exactly the "box plus partial reading plus confidence" contract. It also emits ~2% junk: whitespace-only "words" over rules, images and the gutter shadow (e.g. `[0,304,364,469]`, conf 0.95); filter by empty text. Example overlay: `/tmp/claude-1000/-home-gordon-Dokumenty-drop-analysis/a576cbb2-e149-463f-ac7b-f09ccaf65516/scratchpad/ocr-bench/overlays/tess_best_p1_crop.png`.
- PaddleOCR reads more of the smear (`jou`, `avidelné`) but drops leading letters, and its confidence is per line (0.96 for a smeared word), so unreadable words cannot be flagged. Rebuilt word x-positions drift by 10-35 px in the smear.
- docTR finds the smeared blobs as boxes but merges neighbours (`mintosestupové`) and has no diacritics: every pretrained docTR vocabulary lacks all Czech diacritics, even `á í ý ú` (checked all 9 pretrained recognition archs). Not usable for Czech without fine-tuning.
- Surya detector gives the cleanest line boxes including the gutter lines, no junk: `/tmp/claude-1000/-home-gordon-Dokumenty-drop-analysis/a576cbb2-e149-463f-ac7b-f09ccaf65516/scratchpad/ocr-bench/overlays/surya_det_p1_crop.png`.

**Layout and reading order** (judged on p1 and p5 against my own reading):
- Surya 2.5 layout mode (`/tmp/claude-1000/-home-gordon-Dokumenty-drop-analysis/a576cbb2-e149-463f-ac7b-f09ccaf65516/scratchpad/ocr-bench/overlays/surya2_layout_p1_blocks.png`, `..._p5_blocks.png`) is close to human order: headings before text, column 1 then column 2 inside an article, three-column article on p5 followed correctly, pictures found. Weaknesses: the masthead is split into fragments; **coverage is not reliable**: layout mode missed the whole "Obecně závazná vyhláška" article on p1 (82% of detected lines covered), and full-page mode covered only 20% of p5 lines (8 blocks). The two modes fail on different pages, so a coverage check against detected text lines is mandatory; uncovered lines must be handled by a fallback (I used orphan grouping, 152 words over 6 pages).
- PP-StructureV3 covers 100% of lines and finds the missing article, but the order is poor: on p1 "K novému Domu" body comes before its heading, and on p5 the last paragraph of column 1 comes after column 3 and the list is shattered into small blocks (`/tmp/claude-1000/-home-gordon-Dokumenty-drop-analysis/a576cbb2-e149-463f-ac7b-f09ccaf65516/scratchpad/ocr-bench/overlays/ppstructv3_v5_gpu_p1_blocks.png`, `..._p5_blocks.png`).
- Tesseract's native order interleaves the two sub-columns on p1 (page-order CER on region C 42% against 1.2% for Surya full page and 3.7% for Tesseract words reordered by Surya layout).
- Combination "word engine + Surya layout order" (`scripts/combine.py`): page-order CER C = 3.7% (Tesseract), 2.2% (Surya lines + Tesseract), 1.4% (PaddleOCR), vs 40% for PaddleOCR with PP-StructureV3 order.

**Surya 2.5 gotcha**: its vocabulary is character-level but vLLM detokenises with a byte-level decoder, so every Czech diacritic came back as control chars or U+FFFD (`věku` -> `v\x1bku`). Fixed by re-decoding the returned token ids with the literal token strings (`return_token_ids`, see `scripts/run_surya2.py`). Also vLLM needed `VLLM_USE_FLASHINFER_SAMPLER=0` because there is no `nvcc`. Surya's default vLLM backend wants Docker; llama.cpp needs a `llama-server` binary; neither is present, so I ran pip vLLM manually. This is heavy for a local pipeline.

## Install requirements and sudo

- Nothing here needed sudo. Tesseract works through the `tesserocr` wheel plus `ces.traineddata` downloaded from GitHub. A system install would be `sudo pacman -S tesseract tesseract-data-ces` (and `ocrmypdf`) if the owner prefers the CLI.
- A micromamba download was blocked by the sandbox policy, so conda-forge tesseract was not tried; not needed.
- Python 3.14 (system) has no wheels for torch/paddle: use 3.12 (uv).
- Caches outside the scratchpad that can be deleted afterwards: `~/.cache/ocr-bench-heavy` (venvs ~28 GB incl. vLLM, uv cache, HF cache), `~/.paddlex`, `~/.cache/doctr`, `~/.cache/datalab`. /tmp is a 16 GB tmpfs and filled up during installs, which is why venvs live on /home (symlinked from the scratchpad).

## Not finished / limits

- PaddleOCR CPU: v5 OCR only ~55 s/page with `enable_mkldnn=False` (default oneDNN crashes); the PP-StructureV3 CPU run and Surya detector CPU timing were not completed. Surya VLM CPU is impractical.
- Surya "block OCR" mode (layout then per-block OCR) was not run; only layout and full-page modes.
- PaddleOCR char boxes and Tesseract hOCR output were not compared; Tesseract char boxes were only counted.

## Recommendation

Use a combination, all local:

1. **Words, boxes, confidence: Tesseract 5 `ces` (tessdata_best), full page or per detected line.** Best box tightness (IoU ~0.95), real word and char boxes, honest per-word confidence, partial readings in smeared words, ~5 s/page CPU, tiny install, Apache-2.0. Drop whitespace-only words. Optionally run Surya's detector first and let Tesseract read each detected line (cleaner at the gutter, no junk boxes, slightly worse on clean text in A).
2. **Layout and reading order: Surya 2.5 layout mode** (best order by far, found articles, headings, pictures), with a mandatory coverage check against detected text lines and a geometric fallback (XY-cut) for uncovered lines. Costs a vLLM server (~5 GB VRAM, ~4 s/page) and the weights licence (research/personal/small startup).
3. **Second reading for AI-bound words: PaddleOCR PP-OCRv5 latin** (0.8 s/page GPU) as an independent line reading when Tesseract confidence is low; it gets `ô` and cleaner text near the gutter but gives no word confidence.

Trade-offs: if the Surya licence or vLLM weight is unacceptable, use Tesseract blocks plus an own column XY-cut, or PP-StructureV3 as a weaker fallback order (its order needs repair). docTR is not recommended for Czech (no diacritics in pretrained vocab). The single-engine option is Tesseract alone: highest box quality and good text, but reading order across columns needs separate work.
