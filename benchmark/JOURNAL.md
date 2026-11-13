# Optimisation journal

Rounds of `/optimize` (`.claude/skills/optimize/SKILL.md`), newest first. Numbers are trial table columns
(`benchmark/README.md`): correct % / silent / geometry / order (runs-words) / review / unnecessary. The full
text of the rounds before 2026-11-13 is in git history (`git log -p benchmark/JOURNAL.md`).

## Baselines

The latest kept trial of each configuration; every round compares against these. All scored with corrections
digest 9217c522 (16 reference corrections).

| Configuration | Trial | correct % | silent | geometry | order | review | unnecessary |
|---|---|---|---|---|---|---|---|
| claude haiku high | 20261111T191400196517Z | 94.82 | 61 | 248 | 15/87w | 315 | 19 |
| codex gpt-6-luna xhigh (blocks) | 20261104T192635628267Z | 96.50 | 88 | 192 | 51/1188w | 169 | 2 |
| check: claude sonnet medium (blocks) | 20261102T204138707446Z | 95.79 | 74 | 219 | 72/541w | 310 | 35 |
| check: codex gpt-6.1-terra medium (blocks) | 20261102T204138881928Z | 97.52 | 67 | 177 | 64/930w | 136 | 1 |

"(blocks)": measured with the layout method of ADR 0005, removed on 2026-11-13 (tag `layout-blocks-last`); these
configurations have no layout-regions trial yet, so their first one becomes their baseline. The last haiku blocks
trial was 20261106T065848552834Z 95.12 / 59 / 221 / 12-681w / 292 / 17. AI time: haiku ~1800 s, 1.2 M tokens;
luna xhigh ~4750 s, 2.2 M tokens.

Noise: with identical problem spots, untouched pages swing by up to ~25 silent in luna and ~6 in haiku (same code
gave luna 105 and 134); haiku's review and unnecessary swing by ~40 / ~13. One baseline trial is no fixed point:
judge a change on the spots it touches (findings whose ids carry the new signal or lie in the changed spots
files) and, when the total moves against them, rerun main as well as the change. Trials may run in parallel.

## Open ideas

Silent errors by cause, last diagnosis 2026-11-02 (edge-remnants baselines, findings haiku 41 / luna 86, blocks
method): confident misreadings at a faded or shadowed edge without a spot 12 / 12; AI guesses at a damaged edge
2 / 24; confident misreadings away from an edge 8 / 7; reconstruction adopts the read-through note's form 0 / 8;
AI corrects the print's typos 1 / 3; AI confirms a wrong reading away from an edge 0 / 8; bullets, initials and
noise 5 / 10; degenerate split boxes 6 / 4; p4 cut line ends 2 / 4. Layout regions (2026-11-11) changed order and
geometry, not these text causes; their own losses are listed in that round's Next.

17. `damaged_edge` spots whose OCR text is right become unnecessary review items when the AI will not confirm
    them (sonnet 26, luna 3-6: p6 day numbers "9." "12."; haiku 2: p3 bullets). Ideas: tell reconstruction a
    damaged_edge spot is a clear reading kept in doubt only by its position; or skip the flag for short numbers
    whose column neighbours read the same pattern. Step: reconstruct / problems.
15. Luna takes the read-through note's suggested form against clear print (names, typos, case endings). A
    prompt rule failed (round note-hypothesis). Ideas: send reconstruction only the reason, not the form; or a
    structural check as in idea 14. Step: readthrough / reconstruct.
14. Visual read-through with a checked candidate (user's idea; targets idea 3 and parts of 4 and 13): give the
    read-through each block's image too, return the likely form as a `candidate` field, and let reconstruction
    accept it only when its crop shows or does not contradict it (else unresolved, candidate pre-filled for
    review). Keeps Tesseract the word source (ADR 0005) and AI on problem spots (ADR 0001). Risks: tokens, false
    flags, the same model flagging and confirming. Also covers idea 0. Step: problems (readthrough) + reconstruct.
13. Small bullets "-" at p3's left edge missing or read as "»", decorative vertical initials on p2 (R-O-S-T)
    missing or "„+", noise beside p6's headings read as a letter. Step: ocr.
12. AI guesses at a damaged page edge (faded left edges p1/p5, binding shadow p2): luna drops faint short words
    or substitutes, cuts line-end heads short; the threshold cannot separate right from wrong (80-99 both).
    Crops showing the remnants did not help (round edge-crop-ink): the AI reads by the sentence. Idea: raise
    the contrast of crops in a dark gradient. Step: reconstruct (crops).
0. Letters cut off at p4's edge ("úrovn|", "květn|"): only the sentence decides; haiku proposes the right form
   at 60-75, luna confirms the OCR reading at 99. Idea: take letters the print does not show from the sentence,
   or a lower threshold when the crop's word touches the page edge. Step: reconstruct.
1. Rest of p4's cut line ends: pairs that are words on both sides ("místo"/"starosty") stay unflagged; luna
   writes a whole word into the head's single box. Idea: split a one-id reading of a line-end spot when the word
   ends with the continuation's reading and the head box is far too narrow. Step: reconstruct.
3. Confident wrong Tesseract readings (dictionary or numeric, confidence >= 70, no signal: čárka/Šárka, "135",
   dot vs comma, "17:5."). Step: ocr/problems; needs a new signal.
4. AI accepts a wrong reading at 80-93, errors flat across 80-95. Codex has far more AI/lookup silent errors
   than haiku (letter swaps, cut words, lost spaces). Step: reconstruct.
5. `reconstruction.measure_words` gives one half of a correctly split word a degenerate box (0.4-1.4 pt) when
   the search window reaches foreign ink. Step: reconstruct.
6. Underlined lines on p2 merge with the line below. Step: prepare (remove underlines) or ocr.
7. AI corrects the print's typos against ADR 0001 (`error: recognition` instead of `intended`).
8. Empty word boxes become illegible markers (~134 review findings: table rules, page edges, next to a word).
   Step: ocr (inkgaps) -> problems/reconstruct.
9. Tables read column by column (p6) and unruled tables inside a layout region: issue #23.
10. Scoring: a reference-free box check (does our box wrap the word's ink snugly?).
11. Approach change, needs the user's yes: AI transcribes whole regions or blocks from the image, mapped back to
    word boxes (the next experiment after 2026-11-13).

Notes on the scoring: which article comes first on a page is partly a judgement call, so judge large order runs
on the order preview before chasing them. Where the scan cuts letters off, the reference guessed; our illegible
markers there are honest (`flagged`). The print's own typos ("záležitosmi", "Sololovny") stay as printed
(ADR 0001). Codex writes a hyphen-broken word whole into the second half's box and invents text at cut edges.

## Rounds

<!-- Format of a round: one short line per field; the details belong in the commit or PR. Once a later
round supersedes it, shorten a round to its hypothesis, headline numbers and lesson.
### YYYY-MM-DD <topic> (branch, kept | not kept)
Hypothesis: cause, step, expected effect.
Trials: before -> after per configuration, with trial ids.
Decision: why kept or not (what to avoid retrying); reference corrections added.
Next: what the numbers suggest.
-->

### 2026-11-11 layout-regions (branch feat/regions-layout, issue #22, kept; default and only method since 2026-11-13)
Hypothesis: AI layout regions before OCR (ADR 0006) cut the words out of reading order without costing text.
Local OCR also reads the page whole and a region takes over words its own reading missed (`with_page_words`;
without it ~62 more words were missing: the binding shadow, page edges, white-on-black imprint).
Trials (haiku high): blocks 20261106T065848552834Z 95.12 / 59 / 221 / 12-681w / 292 / 17 -> regions with page
words 20261111T191400196517Z 94.82 / 61 / 248 / 15-87w / 315 / 19 (two more runs alike). Local OCR 51 -> ~200 s.
Decision: reading order matters more than 0.3-0.5 points of text accuracy (user, 2026-11-13); silent level,
geometry ~25 higher. Luna not run.
Next: p1 marks and noise in the header region and left edge (`with_page_words` takes low-confidence words and
empty boxes as they are, and no page word with text where the region left an empty box); p2 lines y 198-228 of
the right column as illegible markers with two overlapping readings; p6 "Š" read as "č" in the region reading,
dots as commas in tables; then a luna trial, and tables inside a region (#23).

### 2026-11-04 edge-crop-ink (not kept)
Hypothesis: the AI's crop hid ink past the OCR box at damaged edges. Crops reached further and the outline moved
off ink. Trials: haiku 57 -> 53 / 55 silent, luna 88 -> 96 / 90: within noise; on luna's edge spots 5 fixed, 6
new. Decision: the AI sees the remnants and still reads by the sentence; do not retry crop work for idea 12.

### 2026-11-04 effort-xhigh (measurement only, kept for luna)
Effort xhigh instead of high. haiku: no gain beyond noise at 2.2x time and tokens, stays high. luna: two xhigh
runs 98 / 88 silent against 115 / 118 high, unnecessary 4 / 2 against 10, at 3-6x time: luna's iteration
configuration is xhigh (baseline 20261104T192635628267Z).

### 2026-11-02 edge-band (kept)
Words in a damaged edge band (binding shadow, faded or cut-close edges) below confidence 96 are flagged doubtful
(`edgeband`, signal `damaged_edge`; 136 new spots). On the flagged spots silent fell 14 (haiku) and 17 (luna)
against a rerun of main; unnecessary +4 / +6 (idea 17). Haiku 20261102T193945014376Z 95.57 / 57 / 214.

### 2026-11-02 note-hypothesis (not kept)
A prompt telling reconstruction the read-through note's form is a guess made without the print. Silent worse in
all three trials (haiku 58 -> 66, luna 105 -> 123 / 111): fixed the diagnosed ids but made new note-driven errors
at 94-99 elsewhere, as the noted set varies between runs. Decision: a prompt shifts the failure; needs a
structural check (idea 15).

### 2026-11-02 edge-remnants (kept)
The reconstruction prompt reads letter by letter: a reading agrees with every visible letter, keeps faint short
words, answers a line-start continuation as the rest only. Diagnosed edge ids silent haiku 8 -> 2-3, luna 43 ->
18; luna 127 -> 106 silent; review +21-33 as the AI now leaves edge spots unresolved (the goal).

### 2026-11-02 narrow-spaces (kept)
`inkgaps.split_narrow_spaces` cuts a word at an ink-free gap of >= 0.25 letter heights after "." or "," or
between a digit and a letter (measured on ink: Tesseract's char boxes of dots overlap). Silent -20 haiku, -28
luna, geometry -48 / -61, correct % +2.0 / +2.6. Reference corrections: 3.

### 2026-11-01 context-readthrough (kept)
An AI read-through of each page's text names words that do not fit (signal `context`, `ocrmaker.readthrough`);
`reconstruction.split_dropped_rest` keeps a dropped rest. On the read-through's own spots silent fell 11-12 in
all four trials with none new; problems step +170 s.

### 2026-11-01 cut-line-end-hyphens (kept)
Signal `joins_next_line` for a line end that forms a dictionary word with the next line's first word;
`reconstruction.split_line_break` splits a whole-word answer at the line break. p4 silent fell in all five
trials (luna lost hyphens 26 -> 5).

### 2026-11-01 dropped-regions (kept)
Local OCR reads a cleared page again (`inkgaps.cleared_page`) and adds the lines Tesseract's segmentation had
dropped (grey box, headlines). Silent haiku 150 -> 103, luna 201 -> 159. Reference corrections: 3.
