# Optimisation journal

Rounds of `/optimize` (`.claude/skills/optimize/SKILL.md`), newest first. Numbers are trial table columns
(`benchmark/README.md`).

## Baselines

The latest kept trial of each configuration; every round compares against these.

| Configuration | Trial | correct % | silent | geometry | order | review | unnecessary |
|---|---|---|---|---|---|---|---|
| claude haiku high | 20261101T235121 | 93.45 | 92 | 257 | 59/640w | 278 | 8 |
| codex gpt-6-luna high | 20261101T233846 | 93.39 | 158 | 254 | 62/1179w | 163 | 1 |
| check: claude sonnet medium | 20261102T002422 | 93.36 | 106 | 269 | 62/830w | 296 | 11 |
| check: codex gpt-6.1-terra medium | 20261102T003002 | 96.30 | 103 | 205 | 58/735w | 142 | 0 |

Scored with corrections digest of 10 reference corrections (2026-11-01). AI time: haiku 1895 s, luna 730 s;
about 1.1-1.3 M tokens each. Luna's 158 holds ~25 p6 dates written without their spaces ("6.3.2000"), its
usual noise there (idea 2); its other recent trial at this code minus the split was 143. Kept changes since
the last check configurations: 0 (first check run, after the context-readthrough round, code ce62841;
sonnet 315 s AI, 0.4 M tokens; terra 343 s, 0.7 M). The cheap configurations have not plateaued: terra
medium beats luna high by 55 silent and gives no unnecessary review items, sonnet stays near haiku.

Noise: with identical problem spots, the pages a change does not touch still swing by up to ~25 silent in
luna (108, 100, 131, 106 over four trials) and ~6 in haiku. Judge a change on the pages it touches (compare
the spots files) and rerun luna when its total moves against the touched pages.

## Open ideas

Silent errors by cause (diagnosis of 2026-11-01 on the haiku baseline, 150 silent; the local causes are the
same in luna). Done: text Tesseract never found (grey box p1, headline p2, "2000" p1); line-end hyphens cut
off at p4's right edge; AI read-through of the page text (rounds below).

0. Context where the scan cut letters off (next round): at p4's cut edge "úrovn|" and "květn|" lost their
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
2. Lost narrow spaces with digits ("19.2.", "8do", "92let"; 19 + 3 silent): `_known` skips anything with a
   digit and `_UNIT` takes "8do", "92let" for a number with a unit. Step: problems.
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
