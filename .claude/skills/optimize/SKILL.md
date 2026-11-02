---
name: optimize
description: One optimisation round of the automatic workflow, measured on the benchmark.
disable-model-invocation: true
---

# Optimisation round

One **round** = one **hypothesis**, one change, measured by benchmark trials and kept only without
**regression**. The goal: manual review holds only text nobody could determine (truly unreadable numbers,
names not findable even on the web); text, geometry and reading order are right without the user.
Tools and score columns: `benchmark/README.md`. Memory between rounds: `benchmark/JOURNAL.md`.

## Steps

1. **Orient.** Read `benchmark/JOURNAL.md`, run `.venv/bin/python -m ocrmaker.benchmark list`, and check that
   `git status` is clean on `main`. Done when you can name the current baseline trial of each iteration
   configuration (below) and the open ideas.

2. **Choose the hypothesis.** If `benchmark/JOURNAL.md` has a section *Next round (set by the user)*, do that
   round as written instead (no diagnosis, no branch unless it says so), then delete the section.
   Otherwise take the worst class in priority order: silent errors (missing text included)
   → geometry and order → unnecessary review items → cost. Dispatch a general-purpose subagent to diagnose
   it; its prompt names the class, the baseline trial IDs, the scratchpad path, and says: "Follow the section
   *Diagnosis* in `.claude/skills/optimize/SKILL.md`." Stay out of the crops yourself; open one only when the
   report leaves the mechanism unclear. Done when the journal-worthy hypothesis is one sentence naming the
   cause, the step and the expected effect.

3. **Gate approach changes.** A change that contradicts an ADR in `docs/adr/` or replaces how a step works
   (for example AI transcribing whole lines instead of single problem spots) goes to the user first: write
   the proposal with its numbers into the journal, end the round and ask one yes/no question. Prompts,
   thresholds, heuristics, preprocessing and bug fixes go ahead without asking.

4. **Change.** On a branch `optimize/<YYYY-MM-DD>-<topic>`, implement the change as a **generic** rule: it must
   hold for any scanned Czech newsletter, so no words, coordinates or page numbers of a benchmark document.
   Faithful transcription (ADR 0001) stays: the reference is evidence about the print, never a target to
   copy. Add or adjust tests at the repo's seams; `.venv/bin/python -m pytest -q` must pass. Commit.

5. **Measure.** Run the iteration configurations, each with `--label "<topic>"`:

       .venv/bin/python -m ocrmaker.benchmark run --provider claude --model haiku --effort high --label …
       .venv/bin/python -m ocrmaker.benchmark run --provider codex --model gpt-6-luna --effort high --label …

   Trials take minutes; run them in the background. Budget: 6–8 trials per session in all. On a usage limit,
   stop, journal what is measured so far and end the round. After every third kept change, also run the
   check configurations `claude sonnet medium` and `codex gpt-6.1-terra medium`. Expensive models (opus,
   fable, gpt-6.1-sol) only when the journal records that cheap ones have plateaued.

6. **Decide.** Compare with the baseline trials of the same configurations, scored on the same corrections.
   Keep the change when the target class improves and no higher-priority class gets worse in either
   configuration. A difference of a few findings may be AI noise: rerun that configuration once before
   deciding. Kept → commit message carries the before/after numbers, `git merge --ff-only` into `main`,
   delete the branch; the new trials become the baseline. Not kept → leave the branch unmerged, back to `main`.

7. **Journal.** Add the round at the top of the rounds in `benchmark/JOURNAL.md` (format there) and update the
   baselines and open ideas; commit it on `main`. Done when the next round could start from the journal alone.

8. **Report** to the user in Czech, in a few lines: what was tried, the numbers, kept or not, the next idea,
   and the yes/no question if step 3 raised one.

## Diagnosis

Run by the subagent of step 2. Tools: `benchmark/README.md`. On the given baseline trial, list the class's
findings with `show --kind …` and render them with `crops … --out <scratchpad>/crops`; look at the PNGs
(green is the reference, red ours), at most ~20 crops. For every finding you judge on the scan:

- the reference is wrong → add a correction to `benchmark/corrections/<document>.json` (with a `note`),
  then `rescore` the baseline trials;
- the result is wrong → name the cause: which step, which mechanism.

A box is right when it wraps its printed word (or lone character) snugly, so selecting it feels natural,
whatever the reference's box looks like; a small offset from the reference is no finding to chase.

Done when every crop you rendered is judged. Report back only:

1. corrections added: count per document, and the rescored numbers;
2. result errors grouped by cause: step, mechanism, count, two or three finding IDs as examples;
3. the cause group you would attack first, as a one-sentence hypothesis with its expected effect.

## Configurations

| Role | Configurations |
|---|---|
| iteration (every round) | `claude haiku high`, `codex gpt-6-luna high` |
| check (every third kept change) | `claude sonnet medium`, `codex gpt-6.1-terra medium` |
| ceiling (rarely) | the reference itself was made by `gpt-6.1-sol high` over whole pages |
