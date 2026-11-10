# The page is divided into AI regions in reading order before OCR, and the user approves them

Supersedes the reading-order half of ADR 0005; Tesseract stays the single source of words and geometry.

Layout now runs right after image preparation and before Local OCR. One AI request per page (method v8 of the regions
prototype, `benchmark/prototypes/regions/`) returns the page's articles of kind article, header, footer or ad in
reading order, each as one or more regions: rectangles holding one column of one article, read top to bottom, with an
orientation. Regions are snapped to the ink they own and checked against a throwaway whole-page Tesseract pass (words
outside or in two regions, an edge through a line, two columns in one region, overlaps, uncovered ink, a lost line); one
corrective request may follow. Local OCR then recognises each region on its own (rotated by its orientation), and the
local block building of `layout.py` runs inside each region, so the Layout and Reading order results keep their shape
for the later steps. Reading order is left with the links of articles continued across pages, which need the text.

Layout is the first point where a run stops. The user reviews all pages in one scrolling view and edits regions
directly (move, resize, Shift pushes neighbours, draw, delete, reorder by clicking, join to the previous article,
kind, orientation) with an optional note under each page. Every region starts locked; the user unlocks only the wrong
ones. "Zpracovat" sends again only pages with an unlocked region or a note, and the AI may change only unlocked regions
(a page with a note but nothing unlocked only gets new regions); "Schválit" approves the whole document and the run goes
on. Rounds of automatic layout (A) and manual review (M) repeat until approval; a setting approves automatically for
batch work and the benchmark. Editing the layout after approval makes the later steps stale and, per ADR 0004, discards
manual corrections, which the user is warned about.

We chose this because blocks built from Tesseract lines could not be split when one block ran across two articles,
and the user's costly fixes are adding a region and renumbering, not resizing one. Doing the layout first also keeps
each later piece of work (OCR, order inside a region, AI requests) to one smaller logical unit. In the prototype
benchmark (31 pages, three runs) v8 left 0–3 pages needing a costly fix with Claude haiku (high effort, the default)
against 11–15 for the old method. Uncovered text is never rescued automatically: it is shown in review instead, so
nothing is silently assigned to the wrong article. The old method stays selectable by a setting until the program's
benchmark (three trials) shows the new one is not worse.
