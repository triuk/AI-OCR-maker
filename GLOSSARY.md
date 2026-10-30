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
The literal text `[NEČITELNÉ]` written in place of a problem spot that every automatic reconstruction attempt failed to resolve; it is handed to manual review.

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

**Manual correction**:
A change the user makes during manual review. It is kept apart from step results and applied when the project is exported, so it never causes earlier steps to run again. Running an earlier step again discards all manual corrections.
_Avoid_: Edit, fix (too generic)
