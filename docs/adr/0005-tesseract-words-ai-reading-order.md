# Tesseract provides words and geometry; reading order comes from local blocks checked by AI

Status: the reading-order half is superseded by ADR 0006 (layout regions before OCR); its block method was removed
from the code on 2026-11-13 (git tag `layout-blocks-last`). Tesseract as the single source of words and geometry
stands.

Tesseract 5 (`ces`, tessdata_best) is the single source of words, word boxes and per-word confidence. In a one-sample
engine benchmark (6 pages of a two-column Czech newsletter, 200 DPI, gutter blur; Tesseract, PaddleOCR, docTR, Surya)
it had the tightest boxes (IoU ~0.95), real word and char boxes, a confidence that separates right from wrong readings
(0.95 against 0.44-0.6), it still produced boxes with partial readings for blurred words near the binding, ran ~5 s per
page on the CPU and installs without sudo (Apache-2.0). Its own block order is unusable for multi-column pages, so
blocks were built locally from its lines and words, and Claude received one page preview with numbered blocks per page
and returned only their grouping into articles and their order. We rejected Surya's layout model despite its good
ordering because it silently skipped whole articles, needs a heavy vLLM install and has a restrictive weights licence;
docTR has no Czech diacritics in its pretrained vocabulary. PaddleOCR may later be added as a second reading of problem
spots, never as a geometry source.
