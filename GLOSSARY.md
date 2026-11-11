# AI-OCR-maker

Turns scanned printed documents into searchable, selectable PDFs whose text follows the page's logical reading order, using local processing first and AI only where scripts fall short.

## Language

### Documents and outputs

**Project**:
One source PDF together with everything derived from it: workflow step results, reconstructions, manual corrections, history and outputs. A project persists across working sessions.
_Avoid_: Job, run, document (as a name for the whole unit)

**Source PDF**:
The scanned input PDF supplied by the user; the authority on what is printed.
_Avoid_: Original, raw

**Clean working copy**:
The project's copy of the source PDF with an earlier OCR's invisible text layer removed and its scan images untouched. Once it exists, every step and the OCR PDF build on it; the source PDF itself never changes.
_Avoid_: Cleaned source

**OCR PDF**:
The primary output: the source PDF with its scan images unchanged and an invisible text layer added.
_Avoid_: Searchable PDF, result

**Text layer**:
The invisible text in the OCR PDF, made of word boxes placed over the scan in reading order.

**Word box**:
The rectangle covering exactly one printed word or isolated character on the page, carrying that word's text. It exists even when the word cannot be read.
_Avoid_: Bounding box (too generic), token

**Article text**:
The supplementary Markdown output holding the continuous text of each article in reading order, intended for machine processing.
_Avoid_: TXT export, plain text

### Reading and reconstruction

**Reading order**:
The logical sequence in which a person reads the page: whole articles one after another, each flowing through its columns and continuations.
_Avoid_: Text order, z-order

**Layout region**:
A rectangle of a page holding one column of one article (of any kind: article, header, footer, ad), read top to bottom in its orientation. A page's layout regions form one sequence in reading order; each either starts an article or continues the previous one (labels `3a`, `3b`). Local OCR recognises each layout region on its own.
_Avoid_: Block (Layout builds blocks inside a region), part, box; plain "region" (a region note's region is any dragged area)

**Locked region**:
A layout region the AI must keep as it is (shape, kind, article, relative order) when layout is processed again. Every region starts locked; the user unlocks only the wrong ones.

**Layout round**:
One automatic layout (A) followed by the user's review of it (M). Rounds repeat for pages with an unlocked region or a layout note until the user approves the whole document's layout.

**Layout note**:
Text the user writes under a page during layout review, sent to the AI with that page in the next layout round.

**Faithful transcription**:
The text exactly as printed, including the publisher's own mistakes.

**Publisher error**:
A mistake already present in the printed source. It is kept in the transcription and only noted in the report.
_Avoid_: OCR error (that is a recognition mistake)

**Problem spot**:
A word whose local reading is missing, partial or untrustworthy. It is either an unreadable word or a doubtful reading.

**Unreadable word**:
A problem spot whose print is too damaged to read locally, typically blurred text near the binding or page edge; it has a word box but little or no text.
_Avoid_: Illegible word (illegible is the final state after reconstruction fails)

**Doubtful reading**:
A problem spot read clearly but implausibly, such as `Rychva1d`; it may be a recognition mistake or a publisher error.

**Empty word box**:
A word box with no text, added where printed ink lies inside a text line but local OCR recognised no word there; it is always an unreadable word.

**Project dictionary**:
Words a project accepts as correctly spelled although the language dictionary lacks them, such as local names; a project setting.

**Reconstruction**:
Determining the printed text of a problem spot from the image, its partial reading, the surrounding text or an external source.
_Avoid_: Correction (reconstruction never changes legible text)

**Illegible marker**:
The literal text `[NEČITELNÉ]` written in place of an unreadable word that every automatic reconstruction attempt failed to resolve; it is handed to manual review. An unresolved doubtful reading keeps its printed OCR text instead (ADR 0001) and is listed for manual review after the markers.

### Workflow

**Workflow**:
The fixed sequence of steps a project goes through, from import to export.

**Step**:
One stage of the workflow with its own result, log entries and step preview.

**Step preview**:
The source pages shown with an overlay of what one step produced.
_Avoid_: Visualization, debug view

**Manual review**:
The step in which the user resolves remaining illegible markers and checks the automatic result.

**Review queue**:
The list manual review leads the user through: every illegible marker still in force, with its image crop, surrounding text, partial reading and the AI candidate, then every unresolved doubtful reading kept as read. It can optionally continue through the accepted reconstructions, for spot checks.
_Avoid_: Todo list, worklist

**Propagation suggestion**:
The text of an illegible marker the user has resolved, offered for another illegible marker that matches it by partial reading or by context. Accepting it is one action and makes an ordinary manual correction.

**Region note**:
A note the user attaches to a region dragged on the page during manual review. It is kept with the manual corrections and listed in the report; it changes no text.

**Manual correction**:
A change the user makes during manual review: a word's text (typed, or an accepted propagation suggestion) or a region note. The reading order is not one: it is decided in the layout step. It is kept apart from step results and applied when the project is exported, so it never causes earlier steps to run again. Running an earlier step again discards all manual corrections.
_Avoid_: Edit, fix (too generic)

### Evaluation

**Reference transcription**:
An earlier, independently made transcription of a source PDF that is trusted enough to score the automatic result against. It is not an authority: where it disagrees with the print, the source PDF wins and the disagreement is a finding about the reference.
_Avoid_: Ground truth, gold standard, vzor

**Trial**:
One run of the automatic workflow on a document with a reference transcription, under one configuration, scored against that reference.
_Avoid_: Test run, experiment

**Silent error**:
A word of the automatic result whose text differs from the print although manual review does not list it, so nobody will see it. Text printed on the page but missing from the result is a silent error too.

**Unnecessary review item**:
An entry of the review queue whose text could reasonably have been determined automatically, from the image, the context or an external source. Manual review is meant only for text that cannot be determined at all.
