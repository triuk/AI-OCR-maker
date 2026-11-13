# Optimisation journal

Rounds of `/optimize` (`.claude/skills/optimize/SKILL.md`), newest first. Numbers are trial table columns
(`benchmark/README.md`).

## Baselines

The latest kept trial of each configuration; every round compares against these.

| Configuration | Trial | correct % | silent | geometry | order | review | unnecessary |
|---|---|---|---|---|---|---|---|
| claude haiku high | 20261102T193945014376Z | 95.57 | 57 | 214 | 67/917w | 278 | 15 |
| codex gpt-6-luna xhigh | 20261104T192635628267Z | 96.50 | 88 | 192 | 51/1188w | 169 | 2 |
| check: claude sonnet medium | 20261102T204138707446Z | 95.79 | 74 | 219 | 72/541w | 310 | 35 |
| check: codex gpt-6.1-terra medium | 20261102T204138881928Z | 97.52 | 67 | 177 | 64/930w | 136 | 1 |

Scored with corrections digest of 16 reference corrections (2026-11-02). AI time: haiku 1875 s, 1.2 M tokens; luna
xhigh 4751 s, 2.2 M tokens (luna high was 771 s, 1.0 M; switched in round effort-xhigh). Kept changes since the last check configurations: 0. The check ran after the
edge-band round, code c4cf1a1 (sonnet 298 s AI, 0.4 M tokens; terra 414 s, 0.8 M). Against the previous
check (code ce62841, rescored with 16 corrections: sonnet 100/269/296/11, terra 102/205/142/0 silent /
geometry / review / unnecessary), three kept changes took silent -26 in sonnet and -35 in terra and geometry
-50 / -28; sonnet's unnecessary rose to 35, 26 of them `damaged_edge` spots it leaves unresolved though the
OCR text was right (idea 17). The cheap configurations have not plateaued: terra medium beats luna high by
48 silent with one unnecessary item.

Noise: with identical problem spots, the pages a change does not touch still swing by up to ~25 silent in
luna (108, 100, 131, 106 over four trials) and ~6 in haiku; the same code (85165ca) gave luna 105 and 134,
haiku 58 and 70. One baseline trial is therefore no fixed point: judge a change on the spots it touches
(compare the findings whose ids carry the new signal or lie in the changed spots files) and, when the total
moves against them, rerun main as well as the change. Running trials in parallel
and `list`/`show` during a trial are safe since 3d1fe3a; starting two at the same moment since 0d52d2c.
Luna xhigh on main-equivalent code (round edge-crop-ink): 88 and 96 silent. Before that, opening the library deleted the staged
results of a running trial; only the two trials of 2026-11-02 were hit, and earlier trials are complete.
Reruns of the baselines on aad2f51 (round effort-xhigh): haiku 20261104T175506874986Z 54/223/67-671w/321/28,
luna 20261104T175507041042Z 118/196/55-1454w/139/10; haiku's review and unnecessary swing by ~40 / ~13
between runs of the same code.

## Open ideas

Silent errors by cause (diagnosis of 2026-11-01 on the haiku baseline, 150 silent; the local causes are the
same in luna). Done: text Tesseract never found (grey box p1, headline p2, "2000" p1); line-end hyphens cut
off at p4's right edge; AI read-through of the page text; numbers merged over narrow spaces (rounds below).

Diagnosis of 2026-11-02 on the context-readthrough baselines (haiku 91 / luna 155 silent): edge damage together
(ideas 0, 3 at an edge, 12) 18 / 39, narrow spaces 18 / 42 (now done), confident misreadings away from an
edge 16 / 12, AI accepts a wrong reading away from an edge 7 / 15, line-end splits 2 / 10, degenerate split
boxes 5 / 8, the underlined line p2 6 / 6, small bullets "-" at p3's left edge 8 / 10 (idea 13).

Diagnosis of 2026-11-02 on the narrow-spaces baselines (haiku 71 / luna 127), counts haiku / luna: AI guesses
at a damaged edge 7 / 39 (prompt fixed in round edge-remnants), confident Tesseract misreadings at an edge
15 / 12 and away from it 11 / 9 (idea 3), bullets, initials and noise 14 / 16 (idea 13), AI accepts a wrong
reading away from an edge 6 / 23 (ideas 4, 7), the underlined line p2 6 / 6 (idea 6), degenerate split boxes
3 / 8 (idea 5), p4 cut line ends 3 / 4 (ideas 0, 1). No reference corrections were needed in 20 crops.

Diagnosis of 2026-11-02 on the edge-remnants baselines (findings, haiku 41 / luna 86): confident Tesseract
misreadings at a faded or shadowed edge, no spot (idea 3 at an edge) 12 / 12; AI guesses at a damaged edge
(idea 12) 2 / 24, about 6 of luna's taking the read-through note's form; confident misreadings away from an
edge (idea 3) 8 / 7; reconstruction adopts the note's form against clear print (idea 15) 0 / 8; AI corrects
the print's typos (idea 7) 1 / 3; AI confirms a wrong reading away from an edge (idea 4) 0 / 8; bullets,
initials and noise (idea 13) 5 / 10; degenerate split boxes (idea 5) 6 / 4; narrow-space leftovers (idea 2)
1 / 3; p4 cut line ends (ideas 0, 1) 2 / 4; a broken word written whole into the continuation box when the
head ends the previous column (idea 1) 1 / 1; underlined line p2 (idea 6) 0 / 1. 33 of luna's 86 silent
findings carry a read-through note, 5 of haiku's 41. Reference corrections added: 3 (print typos p2
"čarodejnic,", p3 "pojišťence" twice).

17. `damaged_edge` spots whose OCR text is right become unnecessary review items when the AI will not
    confirm them: sonnet medium 26, luna 3-6 (p6 day numbers "9." "12." in the shadow), haiku 2 (p3 bullets).
    Ideas: the reconstruction prompt says a damaged_edge spot is a clear reading kept in doubt only by its
    position, so a crop that shows the same letters confirms it; or skip the flag for short numbers whose
    neighbours in the column read the same pattern. Step: reconstruct / problems.
16. Done (round edge-band). Left over: p6-w609 lies just outside p6's right band; the two cut line-end
    hyphens at p4's edge ("místo", "spolu", confidence 96) are not flagged (idea 1). Pages 4 and 5 are
    nearly binary scans, so only the cut-close margin finds their band, not the paper level.
15. Luna takes the read-through note's suggested form against clear print (names, the print's typos, case
    endings; 8 + 3 + ~6 at edges). A prompt rule failed (round note-hypothesis): the set of noted spots
    changes from run to run and luna confirms note forms at 98-99 on other spots. Ideas: do not send the
    suggested form to reconstruction (only the reason without a form), or a structural check as in idea 14
    (accept a form different from the local reading only when an independent reading of the crop, made
    without the note, gives the same letters). Step: readthrough / reconstruct.

12. AI guesses at a damaged page edge, rest (haiku 3, luna 18 of the diagnosed ids still silent): p1 and p5
    have faded left edges, p2's right edge lies in the binding's shadow. The AI sees the remnants (200 dpi
    import raster, crop not cut by the edge), and luna still drops faint short words ("o růst" -> "o", "a
    hostů" -> "hostů") or substitutes ("odjezd:" -> "řád:", "června" -> "dubna"). Now it also cuts a line-end
    head short ("připo-" -> "při-", "spolu-" -> "spo-"). The threshold cannot separate these (luna's wrong
    readings at 80-98, right ones at 82-99). Ideas: raise the contrast of crops whose spot lies in a dark
    gradient (p2's binding shadow). Tried (round edge-crop-ink, not kept): crops showing the ink past the
    last OCR box and an outline that clears ink fixed as many edge spots as they broke; the AI sees the
    remnants and still reads by the sentence. Step: reconstruct (crops).
14. Visual read-through with a checked candidate (user's idea; targets idea 3, 26 / 21 silent, and part of
    ideas 4 and 13). The read-through sees only text, so misreadings that look plausible as text stay silent
    ("135" for "11.35", "8," for "8.", "Fůgnera", "Slížková", a wrong case). The read-through already gets
    the whole page's text by article in reading order; give it in addition each paragraph's (Layout block's)
    image, not each line's (a line alone hides the print's context: typeface, alignment, the lines around it,
    at a similar token cost; reconstruction already renders paragraph crops). It names words that disagree
    with the print or the sentence and gives
    the most likely form as a separate `candidate` field (today it is only free text in `reason`, which
    reconstruction gets as a note that is "no evidence"). Reconstruction checks the candidate against its
    crop: the print shows it, or does not contradict it while the context alone determines it -> accept; the
    print shows other letters -> keep the print; the print does not decide and the context allows several
    forms -> unresolved, with the candidate as a pre-filled suggestion for review. This keeps Tesseract the
    source of words (ADR 0005) and AI on problem spots only (ADR 0001), so no user approval is needed, unlike
    idea 11. Risks: tokens and time (page images; haiku already ~30 min), false flags raising unnecessary
    review, and the same model flagging and confirming (luna confirms wrong readings at 98-99). It also covers
    idea 0 (letters cut off at an edge, which only the sentence can decide). Step: problems (readthrough)
    + reconstruct.
13. Small bullets "-" at p3's left edge are missing or read as "»" (8 / 10 silent), decorative vertical
    initials on p2 (R-O-S-T) are missing or read as "„+" (3 / 3), and noise beside p6's headings is read as a
    letter ("KULTURNÍ A", "M ZEMŘELI"; 2 / 3). Step: ocr.

0. Context where the scan cut letters off: at p4's cut edge "úrovn|" and "květn|" lost their
   last letter, so only the sentence can decide ("úrovni", "května"). The read-through names them; haiku
   then proposes the right form but at 60-75 (unresolved, review), luna confirms the OCR reading at 99
   (silent). Idea: tell reconstruction that letters the print does not show (cut off at an edge) are taken
   from the sentence, with the note's form as the candidate; or accept a context spot's reading at a lower
   threshold when the crop's word touches the page edge. Step: reconstruct. About 4-6 silent per config.
1. Rest of the cut line ends on p4 (about 5 silent per config): "místo"/"starosty" and "spolu"/"pracuje" are
   words on both sides, so `_lost_hyphens` leaves them (flagging every such pair would flag clean print like
   "do opravených"); with the hyphen cut off, the sentence's meaning decides (the read-through now names
   them; haiku fixed "místo-", "spolu-" stays), or flag such a pair where the same column edge already has
   lost hyphens. Luna writes a whole word into
   the head's single box ("zů" -> "základní", "trvů" -> "trvání", which `split_line_break` does not see as it
   names one id); a misread continuation ("vidclně") cannot be split. Idea: split a one-id reading of a
   line-end spot too when the word ends with the continuation's reading and the head box is far too narrow
   for the whole word. Step: reconstruct.
2. Lost narrow spaces with digits: done (round narrow-spaces). Left over: "17:5." (a colon for a dot), a comma
   for a dot ("8," for "8."); these belong to idea 3.
3. Confident wrong Tesseract readings (35 silent: čárka/Šárka, květnu, "135", "6.2.2000", dot vs comma):
   dictionary or numeric with confidence >= 70, no signal. Step: ocr/problems; hard, needs a new signal.
4. AI accepts a wrong reading with confidence 80-93 (10 silent, 6 of them at an edge; errors are flat across
   80-95, so a higher threshold does not separate them). Codex has 84 AI/lookup silent errors (vs 28 haiku),
   43 at an edge: letter swaps, cut words, lost spaces. Step: reconstruct.
5. `reconstruction.measure_words` gives one half of a correctly split word a degenerate box (0.4-1.4 pt): the
   search window reaches past the original box and the last gap to foreign ink beats the narrow inner gap
   (8 findings, text right, geometry wrong). Seen again on p4 "najiž" -> "na" + "již": "na" lands on "1925"
   (scored as two silent findings in both configs, the text is right). Step: reconstruct.
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

### 2026-11-11 layout-regions (branch feat/regions-layout, issue #22; "regions" became the default on 2026-11-13)
Hypothesis: AI layout regions decided before OCR (ADR 0006; `layout_method` "regions", layout approved
automatically) cut the words out of reading order without costing text; steps regions, ocr, layout, order.
Change: with regions the benchmark base holds only Import and Image preparation and each trial runs Layout,
Local OCR and Blocks itself. The first trials had about 62 more missing words than the baseline (81-83
against 20; 77 of them had no box in Local OCR's result), the same ones in all three, all inside a region and with no box at all: the years in the binding's shadow at a region's edge (p6),
words at the page edges of p1 and p2, the whole imprint (white letters on a black bar, p6). Tesseract reads a
region alone on its paper canvas worse than on the page, where it leaves a word or an empty word box there.
So Local OCR now also reads the page whole and a region takes over the words its own reading does not cover
(`ocr_step.with_page_words`); that brought back all 77 on the stored pages.
Trials (claude haiku high, corrections 9217c522; correct % / silent / geometry / order / review / unnecessary):
blocks, 7defbd0: 20261106T065848552834Z 95.12 / 59 / 221 / 12-681w / 292 / 17.
regions, e5e9c8b: 20261111T181737293664Z 93.82 / 125 / 219 / 24-124w / 261 / 14, 20261111T182005068461Z
94.59 / 126 / 215 / 24-126w / 219 / 12, 20261111T182010134490Z 94.36 / 125 / 213 / 24-124w / 235 / 10.
regions with the page words, b2a87ed: 20261111T191400196517Z 94.82 / 61 / 248 / 15-87w / 315 / 19,
20261111T191530115902Z 94.80 / 64 / 245 / 13-85w / 322 / 19, 20261111T191535142375Z 94.62 / 61 / 251 /
15-86w / 305 / 14.
AI time 1770-1842 s as before (Layout +230-270 s, Reading order 207 -> 11 s); Local OCR 51 -> ~200 s.
Decision: first kept behind `layout_method` with "blocks" as the default; on 2026-11-13 the user decided that reading
order matters more than the 0.3-0.5 points of text accuracy, so "regions" is the default and "blocks" stays
selectable (the comparison baseline). Order is far better by words
and stable, though not by the number of runs (12 -> 13-15; 681 -> ~86 words out of order; p1 274 -> 4, p2 346 -> 41, the 40 on p5 and p2 are left) and silent is
level, but correct % is 0.3-0.5 points lower in all three trials (haiku's blocks trials since 2026-11-04:
95.02-95.57) and geometry about 25 higher (211-223 before): p1 extra 7 -> ~20 and box 5 -> ~12, p2 merged
3 -> ~9 and silent 5 -> ~12, p6 correct 761 -> ~745 with flagged 35 -> ~48. Part of the geometry is the price of the page words: against the
first regions trials missing fell 82 -> 15 while split rose 16 -> ~62 (the blocks baseline has 59) and extra
79 -> ~85. No reference corrections. Only
haiku was run (the issue's scope); luna was not.
Next: the losses by page. p1: marks and noise words in the header region and at
the left edge that the page reading does not have or that are taken over from it (`with_page_words` takes
low-confidence words and empty boxes as they are, and a page word with text is not taken where the region
reading left an empty word box). p2: the lines at y 198-228 of the right column come out
as illegible markers and two readings overlap there. p6: capital "Š" read as "č" in the region reading
("člížková", "čeligová"), dots read as commas in the tables. Then a luna trial, and tables inside a region (#23).

### 2026-11-04 edge-crop-ink (branch optimize/2026-11-04-edge-crop-padding, not kept)
Hypothesis: at a damaged edge the AI's crop hides the ink the OCR box missed, so luna confirms or guesses a
line end ("55" -> "54", "sportů." -> "sportů,"; edge guesses 26 luna / 6 haiku silent in the diagnosis);
showing that ink should remove most of them (reconstruct, `render_crop`).
Change: first (584b95c) the crop was padded with white beyond the raster so the outline no longer clamped
onto a word at the scan's edge; that touches only 7 spots of this document (boxes within 5 px of the raster
edge), none of them silent in any trial, so the diagnosis's mechanism was wrong: the diagnosed words lie
28-56 px from the raster edge. The crop (a3ce556) instead cut off ink lying beyond the last OCR box: it
ended 0.4 line heights past the boxes, and p4's second "5" lies 12-15 px past its box. The crops now reach 1
line height beyond their ends, an outline side that would cross ink moves out by up to 0.25 line heights,
and the outline is drawn on paper only.
Trials (silent / geometry / order / review / unnecessary, correct %; digest 9217c522): haiku baseline
20261102T193945014376Z 57/214/67-917w/278/15 95.57, rerun 20261104T175506874986Z 54/223, padding
20261104T212829602877Z 53/222/67-644w/308/18 94.82 -> 20261104T220842059464Z 55/211/66-667w/274/23 95.32.
luna baseline 20261104T192635628267Z 88/192/51-1188w/169/2 96.50, padding (a rerun of main in effect)
20261104T212829743437Z 96/186/52-1220w/159/7 96.52 -> 20261104T220741749434Z 90/192/53-1204w/167/10 96.45.
Decision: not kept: totals within noise in both configurations. On luna's edge spots it fixed five ("55",
"výrobků-", "vzteklině.", "Tonerovi,", "května") and made six new ones (p5 line starts at the faded left edge
"užíván" -> "využíván", "že tak" -> "tak", "Čejčíka" -> "Ujčíka", "stvu" -> "stvo"; p2 "ního" -> "čního"; p6
"šéfredaktora:" losing its colon); haiku dropped "a" and "i" at faded edges and fixed only non-edge spots.
The crops show the remnants now, but the AI still reads them by the sentence. A haiku trial started at the
same moment as luna failed (opening the library deleted the other trial's staging copy of its base); fixed on
main in 0d52d2c. No reference corrections (22 crops judged).
Next: the edge rest is the AI's reading, not the crop: idea 15 (do not send the note's form) or idea 14
(a checked candidate) rather than more crop work; idea 13 (bullets, 19 / 16) is the largest local group.

### 2026-11-04 effort-xhigh (no branch, measurement only, kept for luna)
Hypothesis: effort `xhigh` instead of `high` lowers silent errors in the two iteration models without
changing code; set by the user.
Change: none (main aad2f51, same local steps as d77e673). Both providers accepted `xhigh`. Four trials ran
in parallel, so AI seconds are inflated alike (luna's high rerun took 1544 s against the baseline's 771 s).
Trials (silent / geometry / order / review / unnecessary, correct %, AI s, tokens), corrections digest
9217c522 for all:
haiku high baseline 20261102T193945014376Z 57/214/67-917w/278/15 95.57 1875 s 1.18 M, rerun
20261104T175506874986Z 54/223/67-671w/321/28 94.82 1819 s 1.25 M -> xhigh 20261104T175506243730Z
53/215/67-643w/303/27 95.38 4135 s 2.64 M.
luna high baseline 20261102T195349018645Z 115/197/55-1309w/173/10 95.88 771 s 1.05 M, rerun
20261104T175507041042Z 118/196/55-1454w/139/10 95.71 1544 s 0.91 M -> xhigh 20261104T175506688240Z
98/188/54-1352w/150/4 96.68 4831 s 1.72 M.
Silent per page (`silent` kind only; `missing` stays ~20 in all), baseline / rerun / xhigh: haiku p1 8/7/6,
p2 7/6/7, p3 4/5/2, p4 4/7/6, p5 4/3/3, p6 10/6/9; luna p1 20/15/14, p2 12/17/16, p3 14/7/9, p4 16/19/12,
p5 14/20/14, p6 18/20/13.
Decision: haiku: no gain; xhigh's -1 to -4 silent is within the page-level spread of the two high runs, at
2.2x time and tokens; keep `high`. luna: silent -17 / -20, geometry -9 / -8, unnecessary 4, best correct %
so far, spread over p1, p4, p5, p6; beyond the 3 between the two high runs, but within luna's historical
swing of ~25 (Noise), at 3-6x time and 1.6-1.9x tokens. The user asked for a second luna xhigh trial,
switching only if it confirmed: 20261104T192635628267Z (code 781477c, journal only) 88/192/51-1188w/169/2
96.50, 4751 s, 2.19 M; silent kind per page p1 14, p2 7, p3 8, p4 13, p5 16, p6 10 (68). Both xhigh runs
(98, 88) lie below both high runs (115, 118) by more than their own spread, with unnecessary 4 / 2 against
10. Switched: the luna iteration configuration is now `xhigh` (skill and Baselines), its baseline the second
xhigh trial; haiku stays `high`. No reference corrections.
Next: idea 12 / 17 / 13 with the new luna baseline; a luna round now costs ~80 min.

### 2026-11-02 edge-band (branch optimize/2026-11-02-edge-band, kept)
Hypothesis: words in a damaged edge band (binding shadow p2/p6, faded or cut-close edges p1/p4/p5) are
misread by Tesseract at confidence 70-96 and get no spot, so they stay silent (12 / 12); holding words in such
a band to confidence 96 in the problems step should remove about 11 per config at ~70 extra doubtful readings.
Change: `edgeband` finds a band per page side from the raster, in median line heights h: the inner end of the
last h/2 strip within 8 h whose paper level (90th percentile of grey over the text rows) is below 0.95 of the
central half's, plus 1 h; or, when the 10th percentile of the lines' outer margins is below 2 h (print runs
to the scan's edge), that margin plus 1.5 h. Problem detection flags words in the band below confidence 96
as doubtful (signal `damaged_edge`). 136 new spots on this document: p1 18, p2 17, p3 16, p4 37, p5 19, p6 29.
Trials (silent / geometry / order / review / unnecessary, correct %): haiku baseline 20261102T161353103337Z
58/221/68-642w/286/11 95.25 and its rerun on main 20261102T201223279086Z 70/212/68-921w/255/10 95.27 ->
20261102T193945014376Z 57/214/67-917w/278/15 95.57. luna baseline 20261102T154216986962Z
105/186/56-1362w/164/3 96.04 and its rerun on main 20261102T201223120991Z 134/209/74-1109w/137/3 95.62 ->
20261102T193945422575Z 118/207/55-1572w/174/9 95.66 and 20261102T195349018645Z 115/197/55-1309w/173/10 95.88.
Decision: kept. Against the old baseline luna's total rose in both runs, but on the flagged spots silent fell
every time (haiku -11/+0, luna -11/+6 and -11/+5) while the untouched spots moved like AI noise (names, print
typos, foreign diacritics on spots unchanged since the baseline). The rerun of main settled it: 105 was luna's
luckiest run; against the rerun the flagged spots lose 14 (haiku) and 17 (luna) silent. Of the 12 diagnosed
haiku ids 11 are fixed. Cost: unnecessary +4 / +6 (p6 day numbers "9." "12.", p3 bullets "-", left unresolved),
review +20 in luna. New silent on flagged spots in luna are edge guesses (idea 12): "135" -> "15.35",
"trvů" kept, "W)" -> "19". No reference corrections (17 crops judged).
Check configurations (c4cf1a1, third kept change since ce62841): sonnet 20261102T204138707446Z
74/219/72-541w/310/35 95.79, terra 20261102T204138881928Z 67/177/64-930w/136/1 97.52.
Next: idea 12 (luna's edge guesses, now the main edge rest), idea 17 (sonnet's 26 unnecessary edge items) or
idea 13 (bullets, initials, noise).

### 2026-11-02 note-hypothesis (branch optimize/2026-11-02-note-hypothesis, not kept)
Hypothesis: luna adopts the read-through note's suggested form against clearly printed letters ("Michela" ->
"Michaela", "III." -> "I.", "říjnu" -> "října", print typos corrected), because the prompt only calls the
note "no evidence"; telling reconstruction that the form is a guess made without the print, to be given only
when every visible letter agrees, should remove most of these (8 + 3 + ~6 luna silent; reconstruct step).
Change: the reconstruction prompt's context rule: a note on any spot was written from the text alone; compare
its form with the print letter by letter, the print wins on any differing letter, names and the publisher's
typos stay as printed, and a print too faint to decide gets the print's reading at a low confidence.
Trials (silent / geometry / order / review / unnecessary, correct %; all with 16 corrections): haiku
20261102T161353103337Z 58/221/68-642w/286/11 95.25 -> 20261102T180400645151Z 66/217/69-704w/326/16 94.61.
luna 20261102T154216986962Z 105/186/56-1362w/164/3 96.04 -> 20261102T180401013504Z 123/198/54-1229w/144/3
95.80 and 20261102T181902808379Z 111/205/55-1350w/204/20 95.45.
Decision: not kept: silent worse in all three trials, review and unnecessary up in haiku and the luna rerun.
On noted spots luna fixed the diagnosed ids in both runs (Michela, III., říjnu, statečně, FS a) but made 14
and 15 new note-driven errors at 94-99 on other noted spots ("pojišťence" -> "pojištěnce", "Rychvaldě" ->
"Rychvaldů", "května" -> "květnu", "Šlížková" -> "Slížková"); noted silent 32 -> 29 / 28 in luna, 5 -> 5 in
haiku. The rest moved against: non-noted spots 27 -> 46 / 38 in luna, 6 -> 12 in haiku (accepted at 80-88),
and haiku left ~40 more spots unresolved. The read-through's noted set itself varies between runs, so a
prompt shifts which noted spots fail instead of removing the failure. No reference corrections in the round
(the diagnosis added 3 on main; the baselines above are rescored with them).
Next: idea 16 (flag the damaged edge band, 12 / 12, local and deterministic); idea 15 needs a structural
check rather than a prompt.

### 2026-11-02 edge-remnants (branch optimize/2026-11-02-edge-remnants, kept)
Hypothesis: at a damaged page edge AI reconstruction sees the letter remnants in its crop but fills in a word
that fits the sentence, drops faint short words or writes a broken word whole into its continuation's box,
because the prompt only says "print first, context second". Requiring every reading to agree with the visible
letters should remove most of the edge group (7 haiku / 39 luna silent) in the reconstruct step.
Change: the reconstruction prompt says to read letter by letter. A reading must agree with every visible
letter (count, case of the first letter, punctuation), even faint or partly cut ones. It must not put in a
word that fits better or copy one from the surrounding text, and it must keep faint short words inside a box
and look just outside the red outline. The broken-word rule now also covers a line-start continuation: it is
answered as the rest only ("panou", never "kopanou").
Trials (silent / geometry / order / review / unnecessary, correct %): haiku 20261102T091553204461Z
71/209/69-670w/253/11 95.43 -> 20261102T154216987089Z 66/217/69-522w/284/10 94.89 and 20261102T161353103337Z
61/221/68-642w/286/11 95.20. luna 20261102T091553584124Z 127/193/55-1561w/143/4 96.00 ->
20261102T154216986962Z 106/186/56-1362w/164/3 96.02.
Decision: kept. Of the diagnosed edge ids, haiku leaves 2 and 3 silent (was 8), luna 18 (was 43). Luna lost 49
silent findings and gained 28, spread over all pages: p6 names "Šimona" -> "Simona", p3 "pojišťence", line-end
heads cut short on p2. Review rose by 31-33 in haiku and 21 in luna: edge spots the AI now leaves unresolved
instead of guessing, which is the goal. Geometry moved +8 / +12 in haiku (split p1, merged p5, within noise)
and -7 in luna. No reference corrections.
Next: idea 13 (bullets, initials and noise, 14 / 16) or idea 3 (confident misreadings, 26 / 21), both need a
local ocr signal; the cheap follow-up to this round is the crop contrast and outline offset of idea 12.

### 2026-11-02 narrow-spaces (branch optimize/2026-11-02-narrow-spaces, kept)
Hypothesis: Tesseract merges a number with its neighbour across a narrow space ("19. 2. 2000" -> "19.2.2000",
"8 do" -> "8do"; the scan shows a gap of 0.3-0.56 letter heights). Luna confirms such words at 98, so the
errors stay silent. Cutting such words at the gap in Local OCR should remove most of the 18 haiku and 42 luna
silent errors (ocr step).
Change: `inkgaps.split_narrow_spaces` runs in Local OCR after the recovered lines and before the empty word
boxes. It cuts a word at an ink-free gap of at least 0.25 letter heights after "." or "," or between a digit and
a letter. Tesseract's char boxes only assign the gap to a char boundary, and a dot or comma stays left of the
gap. A part takes its lowest char confidence, and the words are numbered again. The first version measured the
gap between char boxes (threshold 0.33) and missed about 20 dates: the LSTM box of a dot overlaps the next char.
The parts also kept the merged word's low confidence, which sent right parts to review (luna unnecessary 1 -> 17).
Trials (silent / geometry / order / review / unnecessary, correct %): haiku 20261101T235121
91/257/59-640w/278/8 93.46 -> char boxes 20261102T084146135440Z 91/243/65-635w/249/7 94.48 -> ink
20261102T091553204461Z 71/209/69-670w/253/11 95.43. luna 20261101T233846 155/254/62-1179w/163/1 93.45 ->
char boxes 20261102T080503181010Z 128/238/56-1515w/209/17 and 20261102T084146228339Z 150/225/52-1047w/172/8 ->
ink 20261102T091553584124Z 127/193/55-1561w/143/4 96.00. Two more trials, haiku 20261102T080502868245Z and
luna 20261102T082433320970Z, are void: another process opening the library deleted their staged AI
reconstruction pages. Fixed in 3d1fe3a; trials now also record the commit they started on (ada48f8).
Decision: kept. Silent fell 20 in haiku and 28 in luna, geometry 48 and 61, correct % up 2.0 and 2.6. On p6
(the dates) luna's silent went 54 -> 17 and merged 55 -> 4; haiku's silent went 28 -> 11. Order words rose on p6
in every luna trial with the cut (118 -> ~400): one run, the whole "ZEMŘELI ... SRDEČNĚ BLAHOPŘEJEME!" section
placed at another position among the articles. The small runs inside the tables fell (39 -> 31). Haiku's order
moved +10 runs / +30 words, within its noise. Unnecessary +3 in haiku: "říjnu" and "Církvi" are known
read-through doubts, and "15 23." on p1 is untouched by the cut. Reference corrections added: 3 (p6 Katarína,
Benjamín, Mílková).
Next: idea 12 (AI guesses at a damaged page edge, 9 haiku / 28 luna), the largest group left that a generic
rule can attack; then idea 13 (bullets and initials) or idea 3.

### 2026-11-01 context-readthrough (branch optimize/2026-11-01-context-readthrough, kept)
Hypothesis: confident misreadings that are real words ("čárka" for "Šárka", "květnu" for "května") and lost
spaces with digits ("8do") get no local signal, so AI never sees them; an AI read-through of each page's
text naming the words that do not fit, sent to AI reconstruction as problem spots, removes them
(problems + reconstruct), silent about -10 to -40.
Change: Problem detection asks the AI once per page (text only) with the page's text article by article in
reading order, numbered lines (`ocrmaker.readthrough`); the named words become doubtful readings with the
signal `context` and the reason as `note`; reconstruction's prompt says the note is a reason to look, not
evidence. Luna then answered "najiž" as "na" alone and dropped "již" (3x p4);
`reconstruction.split_dropped_rest` keeps the rest of the local reading as the next word.
Trials (b09271b, then ce62841 with the split; silent / geometry / review / unnecessary): haiku
20261101T213809 98/266/284/9 -> 20261101T225310 88/262/284/11 / 20261101T235121 92/257/278/8. luna
20261101T221354 139/260/210/5 -> 20261101T232212 143/236/174/7 / 20261101T233846 158/254/163/1. The
read-through flags ~76 words in haiku, ~168 in luna (most already local spots; ~15 new); problems step +170 s.
Decision: kept. On the spots only the read-through flagged, silent fell in all four trials (haiku 11 and 11
fixed, 0 new; luna 12 and 11 fixed, 0 new; "čárka" -> "Šárka", "8do" -> "8 do", "Rychvaldů" -> "Rychvaldě");
on spots that were local spots anyway the change is neutral within noise. Luna's total rose only by p6 dates
losing spaces (58 of 59 new silent findings off the read-through's spots). Unnecessary +2 in two trials:
right words the read-through doubted ("říjnu", "Církvi") that reconstruction left unresolved. No reference
corrections.
Next: idea 0 (context decides letters cut off at the scan edge); then idea 2 (lost narrow spaces with digits).

### 2026-11-01 cut-line-end-hyphens (branch optimize/2026-11-01-cut-line-end-hyphens, kept)
Hypothesis: the scan cuts p4's right column edge, so line-end hyphens are missing from the image; Tesseract's
dictionary-word heads ("při", "do") are never flagged and luna confirms flagged fragments ("ko") without the
hyphen; flagging line ends that join with the next line and telling the AI to restore the hyphen removes
them (problems + reconstruct), silent about -4 haiku, -15 luna.
Change: problems signal `joins_next_line` (doubtful): a line's last word without a hyphen or closing
punctuation, letters only, that forms a dictionary word with the next line's trusted lower-case first word,
one of the two being no word alone ("do" + "opravených" stays unflagged). The prompt says a broken word is
answered as the part on the spot's line ending with "-", even where the scan cut it off. Luna then answered
6 such words whole over both ids, which was rejected and sent both halves to review (unnecessary 2 -> 6);
`reconstruction.split_line_break` now splits such a reading at the line break when the word ends with the
continuation's local reading.
Trials (b12938c, then 6c975f5 with the split): haiku 20261101T193734 -> 20261101T205623 / 20261101T213809:
silent 103 -> 103 / 98 (p4 15 -> 10 / 11), geometry 263 -> 244 / 266, order 61/912w -> 60/638w / 59/489w,
review 295 -> 275 / 284, unnecessary 5 -> 11 / 9. luna 20261101T200337 -> 20261101T212259 /
20261101T220215 / 20261101T221354: silent 159 -> 142 / 164 / 139 (p4 31 -> 22 / 13 / 13, lost hyphens
26 -> 5), geometry 253 -> 232 / 245 / 260, order 55/1320w -> 56/1339w / 52/1313w / 53/1070w, review
192 -> 186 / 164 / 210, unnecessary 2 -> 6 / 3 / 5.
Decision: kept. Problem spots are identical to the baseline on every page but p4, and p4's silent errors
fell in all five trials; luna 20261101T220215 (131 silent off p4, mostly p6 dates losing their spaces) was
noise, its rerun is back at 106. Geometry and unnecessary move within noise. No reference corrections.
Next: idea 0 (AI read-through of the article text as a problem signal); then idea 2 (lost narrow spaces
with digits), to which luna's p6 dates ("11.2.2000" for "11. 2. 2000") belong.

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
