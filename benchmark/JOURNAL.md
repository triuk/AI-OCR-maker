# Optimisation journal

Rounds of `/optimize` (`.claude/skills/optimize/SKILL.md`), newest first. Numbers are trial table columns
(`benchmark/README.md`).

## Baselines

The latest kept trial of each configuration; every round compares against these.

| Configuration | Trial | correct % | silent | geometry | order | review | unnecessary |
|---|---|---|---|---|---|---|---|
| claude haiku high | 20261101T193734 | 92.95 | 103 | 263 | 61/912w | 295 | 5 |
| codex gpt-6-luna high | 20261101T200337 | 92.93 | 159 | 253 | 55/1320w | 192 | 2 |

Scored with corrections digest of 10 reference corrections (2026-11-01). AI time: haiku 1538 s, luna 603 s;
about 0.9-1 M tokens each. Kept changes since the last check configurations: 1.

## Open ideas

Silent errors by cause (diagnosis of 2026-11-01 on the haiku baseline, 150 silent; the local causes are the
same in luna). Done: text Tesseract never found (grey box p1, headline p2, "2000" p1), see the round below.

1. Line-end hyphens cut off at the right edge of p4 (18 silent, both configs): Tesseract reads "tě", "při",
   "do" (dictionary words, never flagged); the AI confirms the visible "ko" even when it says the word is
   "kopanou". Step: problems. Idea: in `problems.py` next to `_dictionary_verdicts`, flag the last word of a
   line without a hyphen when joining it with the next line's first word gives a dictionary word and that
   first word alone is not one.
2. Lost narrow spaces with digits ("19.2.", "8do", "92let"; 19 + 3 silent): `_known` skips anything with a
   digit and `_UNIT` takes "8do", "92let" for a number with a unit. Step: problems.
3. Confident wrong Tesseract readings (35 silent: čárka/Šárka, květnu, "135", "6.2.2000", dot vs comma):
   dictionary or numeric with confidence >= 70, no signal. Step: ocr/problems; hard, needs a new signal.
4. AI accepts a wrong reading with confidence 80-93 (10 silent, 6 of them at an edge; errors are flat across
   80-95, so a higher threshold does not separate them). Codex has 84 AI/lookup silent errors (vs 28 haiku),
   43 at an edge: letter swaps, cut words, lost spaces. Step: reconstruct.
5. `reconstruction.measure_words` gives one half of a correctly split word a degenerate box (0.4-1.4 pt): the
   search window reaches past the original box and the last gap to foreign ink beats the narrow inner gap
   (8 findings, text right, geometry wrong). Step: reconstruct.
6. Underlined lines on p2 merge with the line below (silent 6 before this round; the cleared page now reads
   most of the underlined text again). Step: prepare (remove underlines) or ocr.
7. AI corrects the print's typos against ADR 0001 (2 silent, `error: recognition` instead of `intended`).
8. Empty word boxes become illegible markers (~134 review findings: on table rules, page edges, next to a
   word). Step: ocr (inkgaps) -> problems/reconstruct.
9. Tables on p6 read column by column (31 order findings, ~400 words); table rules become `|` or markers.
   Step: layout/order.
10. Scoring: a reference-free box check (does our box wrap the word's ink snugly?) would judge geometry by the
    real goal instead of by the reference's boxes.
11. Approach change, needs the user's yes: AI transcribes whole lines or blocks from the image with the
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

### 2026-11-01 dropped-regions (branch optimize/2026-11-01-dropped-regions, kept)
Hypothesis: Tesseract's page segmentation drops whole regions (text on a screened grey background, a headline)
and no empty word box marks them, so ~48 words go missing silently; reading the page again with the recognised
lines erased brings them back (ocr step), silent about -39.
Change: Local OCR reads a cleared page once more (`inkgaps.cleared_page`: lines erased, print screen averaged
twice over 0.12 line heights, each pixel divided by 0.8 x its local paper level, the lightest grey within 1.5
line heights) and adds lines with mean confidence >= 50 and at least 0.6 x the median line height (lower ones
are slivers of letters cut by erasing a neighbour). The ink gap finder measures ink on the same descreened
page with that paper level, so a grey box gives no empty word boxes. Recovered on the benchmark: the grey box
p1, "2000" in the p1 header, the p2 headline, the underlined line p2, two headings p6.
Trials: haiku 20261101T161756 -> 20261101T193734: silent 150 -> 103, geometry 239 -> 263, order 50/1112w ->
61/912w, review 238 -> 295, unnecessary 5 -> 5, correct 93.27 -> 92.95 %. luna 20261101T164325 ->
20261101T200337: silent 201 -> 159, geometry 260 -> 253, order 51/1338w -> 55/1320w, review 200 -> 192,
unnecessary 4 -> 2, correct 92.21 -> 92.93 %.
Decision: kept, silent down in both and no higher class worse. Haiku's geometry and correct % got worse almost
only on p6 (the table page: merged 32 -> 52, flagged 48 -> 66, correct 688 -> 637) while luna's p6 barely moved;
likely haiku noise in reconstructing the table, but if the next haiku trial keeps p6 that low, judge its p6
merged findings on crops. Reference corrections added: 3 (p3 nový, p3 nátkem, p4 Církvi).
Next: idea 1 (cut line-end hyphens, -18 silent in both configs, a local problems-step rule).
