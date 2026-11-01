# Optimisation journal

Rounds of `/optimize` (`.claude/skills/optimize/SKILL.md`), newest first. Numbers are trial table columns
(`benchmark/README.md`).

## Baselines

The latest kept trial of each configuration; every round compares against these.

| Configuration | Trial | correct % | silent | geometry | order | review | unnecessary |
|---|---|---|---|---|---|---|---|
| claude haiku high | – | | | | | | |
| codex gpt-6-luna high | – | | | | | | |

## Open ideas

- AI transcribes whole lines or blocks from the image, Tesseract text as a hint, mapped back to the word
  boxes (approach change: needs the user's yes, step 3). Expected to cut silent errors and review items most.
- Text missing entirely from the result (a header year, text on a grey background) points at image
  preparation or line detection, not AI.
- Underlined lines push word boxes off the words; a word near the binding gets split into several boxes.

## Rounds

<!-- Format of a round:
### YYYY-MM-DD <topic> (branch optimize/YYYY-MM-DD-<topic>, kept | not kept | proposed)
Hypothesis: one sentence: cause, step, expected effect.
Change: what changed, generically.
Trials: before → after per configuration, with trial ids.
Decision: why kept or not; reference corrections added.
Next: what the numbers suggest.
-->
