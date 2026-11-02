# Optimisation journal

Rounds of `/optimize` (`.claude/skills/optimize/SKILL.md`), newest first. Numbers are trial table columns
(`benchmark/README.md`).

## Baselines

The latest kept trial of each configuration; every round compares against these.

| Configuration | Trial | correct % | silent | geometry | order | review | unnecessary |
|---|---|---|---|---|---|---|---|
| claude haiku high | 20261102T161353103337Z | 95.20 | 61 | 221 | 68/642w | 286 | 11 |
| codex gpt-6-luna high | 20261102T154216986962Z | 96.02 | 106 | 186 | 56/1362w | 164 | 3 |
| check: claude sonnet medium | 20261102T002422 | 93.36 | 106 | 269 | 62/830w | 296 | 11 |
| check: codex gpt-6.1-terra medium | 20261102T003002 | 96.30 | 103 | 205 | 58/735w | 142 | 0 |

Scored with corrections digest of 13 reference corrections (2026-11-02; the check rows were scored with 10).
AI time: haiku 1742 s, luna 955 s; about 1.1-1.2 M tokens each. Kept changes since the last check
configurations: 2 (narrow-spaces 689d28e, edge-remnants e970d42); the next kept change runs the check. The check run was after the context-readthrough round, code
ce62841 (sonnet 315 s AI, 0.4 M tokens; terra 343 s, 0.7 M). The cheap configurations have not plateaued:
terra medium beat luna high by 55 silent and gave no unnecessary review items, and sonnet stayed near haiku.

Noise: with identical problem spots, the pages a change does not touch still swing by up to ~25 silent in
luna (108, 100, 131, 106 over four trials) and ~6 in haiku. Judge a change on the pages it touches (compare
the spots files) and rerun luna when its total moves against the touched pages. Running trials in parallel
and `list`/`show` during a trial are safe since 3d1fe3a. Before that, opening the library deleted the staged
results of a running trial; only the two trials of 2026-11-02 were hit, and earlier trials are complete.

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

12. AI guesses at a damaged page edge, rest (haiku 3, luna 18 of the diagnosed ids still silent): p1 and p5
    have faded left edges, p2's right edge lies in the binding's shadow. The AI sees the remnants (200 dpi
    import raster, crop not cut by the edge), and luna still drops faint short words ("o růst" -> "o", "a
    hostů" -> "hostů") or substitutes ("odjezd:" -> "řád:", "června" -> "dubna"). Now it also cuts a line-end
    head short ("připo-" -> "při-", "spolu-" -> "spo-"). The threshold cannot separate these (luna's wrong
    readings at 80-98, right ones at 82-99). Ideas: raise the contrast of crops whose spot lies in a dark
    gradient (p2's binding shadow), and draw the red outline further off the box: at 3 px it covers the faint
    first or last letters that lie just outside the OCR box. Step: reconstruct (crops).
14. Visual read-through with a checked candidate (user's idea; targets idea 3, 26 / 21 silent, and part of
    ideas 4 and 13). The read-through sees only text, so misreadings that look plausible as text stay silent
    ("135" for "11.35", "8," for "8.", "Fůgnera", "Slížková", a wrong case). Give the read-through each
    line's image next to its OCR text; it names words that disagree with the print or the sentence and gives
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
