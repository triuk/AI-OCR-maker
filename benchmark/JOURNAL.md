# Optimisation journal

Rounds of `/optimize` (`.claude/skills/optimize/SKILL.md`), newest first. Numbers are trial table columns
(`benchmark/README.md`).

## Baselines

The latest kept trial of each configuration; every round compares against these.

| Configuration | Trial | correct % | silent | geometry | order | review | unnecessary |
|---|---|---|---|---|---|---|---|
| claude haiku high | 20261101T161756 | 93.21 | 153 | 239 | 50/1112w | 238 | 5 |
| codex gpt-6-luna high | 20261101T164325 | 92.16 | 204 | 260 | 51/1338w | 200 | 4 |

Scored with corrections digest of 7 reference corrections (2026-11-01). AI time: haiku 1502 s, luna 762 s;
about 1 M tokens each.

## Open ideas

Causes from the baseline analysis, by findings explained (haiku):

1. Empty word boxes become illegible markers (~134: flagged, split, extra, box), many on table rules, page
   edges or right next to a word. Step: ocr (inkgaps) → problems/reconstruct.
2. Shadow near the binding and letters cut off at the page edge (~100 flagged, 46 silent, 19 split; p2, p4
   right edge, p5 left edge). Step: prepare. Line-end hyphens near the edge get lost ("ko-" → "ko").
3. Text Tesseract never found (~45 missing): the grey box at the bottom of p1 (32 words), the letter-spaced
   "2000" in the p1 header, the clean headline "V červnu budou rychvaldští hasiči slavit" on p2. Step:
   prepare/ocr. Idea: OCR again the ink no word box covers (inverted or contrast-stretched image, another
   page segmentation mode), or hand it to the AI.
4. Lost narrow spaces ("19.2.", "hosvým", "vRychvaldě."; ~40 merged + silent). Step: ocr/reconstruct.
5. Underlined lines on p2 merge with the line below: boxes off the words, half a line lost (~40). Step:
   prepare (remove underlines) or ocr.
6. Tables on p6 read column by column (31 order findings, 402 words); table rules become `|` or markers.
   Step: layout/order.
7. Scoring: a reference-free box check (does our box wrap the word's ink snugly, without empty margins or a
   neighbour's ink?) would judge geometry by the real goal instead of by the reference's boxes.
8. Approach change, needs the user's yes: AI transcribes whole lines or blocks from the image with the
   Tesseract text as a hint, mapped back to the word boxes.

Notes on the scoring: which article comes first on a page is partly a judgement call, so judge large order
runs on the order preview before chasing them. Where the scan cuts letters off, the reference guessed; our
illegible markers there are honest (`flagged`). The print's own typos ("záležitosmi", "Sololovny") stay as
printed (ADR 0001); an AI that corrects them is wrong. Codex writes a hyphen-broken word whole into the
second half's box and invents text at cut edges.

## Rounds

<!-- Format of a round:
### YYYY-MM-DD <topic> (branch optimize/YYYY-MM-DD-<topic>, kept | not kept | proposed)
Hypothesis: one sentence: cause, step, expected effect.
Change: what changed, generically.
Trials: before → after per configuration, with trial ids.
Decision: why kept or not; reference corrections added.
Next: what the numbers suggest.
-->
