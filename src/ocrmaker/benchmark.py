"""Benchmark: trials of the automatic workflow on documents that have a reference transcription.

A trial runs the workflow on one benchmark document with one configuration (project settings), exports it
without manual corrections and scores the result against the document's reference transcription
(ocrmaker.evaluation). Trials are how changes to the workflow are measured; see benchmark/README.md.

The benchmark directory (default `<data dir>/benchmark`, apart from the user's own library):
    documents/<name>/source.pdf     the scan
    documents/<name>/reference.pdf  the reference transcription: a PDF with a text layer
    library/                        a project store of its own; open it with ./start.sh --data-dir <directory>
    trials/<trial id>.json          one trial record each (TRIAL_VERSION format below)

Reference corrections, the findings about the reference itself, live in the repository:
`benchmark/corrections/<name>.json`, a list in the format of evaluation.read_reference.

A base is a project of one document with only the local steps (local_steps) run. It is kept per document
source, version of the local steps' code and local settings, and every trial starts from a copy of it, so the
local steps run once per change of their code and not once per configuration. With layout regions
(`layout_method` "regions") the AI decides the layout before OCR, so the base ends with Image preparation and
each trial runs Layout, Local OCR and Blocks itself; its layout is approved as it comes (`layout_auto_approve`).

    {"trial": 1, "id", "created", "label", "document", "commit", "code" (code version of the base's steps),
     "settings": the full project settings, "base": base project id, "project": trial project id,
     "failed": {"step", "message"} of the step that stopped the run, or null,
     "steps": [{"id", "state", "duration", "shared" (from the base: one of local_steps(settings))}],
     "ai": the run report's AI totals, "review": the review queue counts (null without an export),
     "corrections": digest of the reference corrections scored with,
     "totals", "pages", "findings": the score (evaluation.compare)}
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pymupdf

from . import evaluation
from . import settings as project_settings
from .diagnostics import new_run_id
from .files import write_json_atomic
from .steps import STEP_IDS, implementations
from .steps.export_step import PLAN_FILE
from .store import ProjectStore, now
from .workflow import FINISHED, FIRST_MANUAL, Workflow

TRIAL_VERSION = 1
LOCAL_STEPS = ("import", "prepare", "regions", "ocr", "layout")
# With layout regions everything from Layout on depends on the AI's answer.
LOCAL_STEPS_REGIONS = ("import", "prepare")
AUTOMATIC_STEPS = STEP_IDS[:FIRST_MANUAL]
# The settings that decide a base: what its steps read, and which steps it has.
LOCAL_SETTINGS = ("language", "layout_method")
PACKAGE = Path(__file__).resolve().parent
REPOSITORY = PACKAGE.parents[1]
CORRECTIONS = REPOSITORY / "benchmark" / "corrections"


def local_steps(settings: dict) -> tuple[str, ...]:
    """The steps a base runs for these settings: those whose result no AI configuration changes."""
    return LOCAL_STEPS_REGIONS if settings["layout_method"] == "regions" else LOCAL_STEPS


def code_version(steps: tuple[str, ...]) -> str:
    """A digest of the code the given local steps run: their modules and every package module those import,
    in turn. A base is rebuilt when it changes; a change to AI steps or scoring keeps the base."""
    pending, files = [f"steps.{step}_step" for step in steps], {}
    while pending:
        module = pending.pop()
        parts = module.split(".")
        path = PACKAGE.joinpath(*parts).with_suffix(".py")
        package = parts[:-1]
        if not path.is_file():
            path, package = PACKAGE.joinpath(*parts, "__init__.py"), parts
        if path in files.values() or not path.is_file():
            continue
        files[module] = path
        for node in ast.walk(ast.parse(path.read_bytes())):
            if isinstance(node, ast.ImportFrom) and node.level:
                base = package[:len(package) - node.level + 1] + (node.module.split(".") if node.module else [])
                pending.append(".".join(base))
                pending += [".".join(base + [alias.name]) for alias in node.names]
    digest = hashlib.sha256()
    for module in sorted(files):
        digest.update(module.encode())
        digest.update(files[module].read_bytes())
    return digest.hexdigest()[:16]


def commit() -> str | None:
    """The repository's commit, with "+dirty" when the working tree has changes; None outside git."""
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPOSITORY, capture_output=True,
                              text=True, timeout=10, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=REPOSITORY,
                               capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return head + ("+dirty" if dirty else "")


class Benchmark:
    def __init__(self, root: Path, *, language=None, corrections: Path = CORRECTIONS):
        """language: the AI providers, as for Workflow (providers.available() in the application)."""
        self.root = Path(root)
        self.documents = self.root / "documents"
        self.trial_dir = self.root / "trials"
        self.corrections = Path(corrections)
        self.store = ProjectStore(self.root / "library")
        self.workflow = Workflow(self.store, language=language)

    # Documents ---------------------------------------------------------------------------------------

    def add_document(self, name: str, source: Path, reference: Path) -> None:
        """Add a scan and its reference transcription; their pages must agree in number and size."""
        if not name or "/" in name or name.startswith("."):
            raise ValueError(f"Invalid document name: {name!r}")
        with pymupdf.open(source) as scan, pymupdf.open(reference) as transcription:
            sizes = [[(round(page.rect.width), round(page.rect.height)) for page in document]
                     for document in (scan, transcription)]
        if sizes[0] != sizes[1]:
            raise ValueError(f"The reference's pages {sizes[1]} do not match the scan's {sizes[0]}.")
        directory = self.documents / name
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, directory / "source.pdf")
        shutil.copyfile(reference, directory / "reference.pdf")

    def document_names(self) -> list[str]:
        return sorted(path.parent.name for path in self.documents.glob("*/reference.pdf"))

    def reference(self, name: str) -> tuple[list[list[dict]], str]:
        """The document's reference transcription with the repository's corrections applied, and a digest
        of those corrections."""
        path = self.corrections / f"{name}.json"
        text = path.read_text(encoding="utf-8") if path.is_file() else "[]"
        pages = evaluation.read_reference(self.documents / name / "reference.pdf", json.loads(text))
        return pages, hashlib.sha256(text.encode()).hexdigest()[:8]

    # Trials ------------------------------------------------------------------------------------------

    def run(self, changes: dict, *, documents: list[str] | None = None, label: str | None = None) -> list[dict]:
        """One trial per document (all by default) with the settings changed by changes; their records."""
        return [self._trial(name, changes, label) for name in documents or self.document_names()]

    def trials(self) -> list[dict]:
        """Every trial record, oldest first."""
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(self.trial_dir.glob("*.json"))]

    def trial(self, trial_id: str) -> dict:
        path = self.trial_dir / f"{trial_id}.json"
        if "/" in trial_id or not path.is_file():
            raise KeyError(f"No trial {trial_id!r}")
        return json.loads(path.read_text(encoding="utf-8"))

    def rescore(self, trial_id: str) -> dict:
        """Score a trial again, after the reference corrections changed; its record is updated."""
        record = self.trial(trial_id)
        record.update(self._score(record["project"], record["document"]))
        write_json_atomic(self.trial_dir / f"{trial_id}.json", record)
        return record

    def _trial(self, name: str, changes: dict, label: str | None) -> dict:
        # A trial measures the automatic result: the AI's layout regions are approved as they come (no person).
        settings = project_settings.merged({}, changes | {"layout_auto_approve": True})
        shared = local_steps(settings)
        # The code the trial runs is the one checked out when it starts; a commit made meanwhile is not.
        code, revision = code_version(shared), commit()
        base = self._base(name, settings, code, shared)
        trial_id = new_run_id()
        project = self.store.duplicate(base, {
            "name": f"{name} · {label or describe(settings)}", "settings": settings,
            "benchmark": {"trial": trial_id}})
        self.workflow.run(project["id"])
        if self._finished(project["id"], AUTOMATIC_STEPS):
            self.workflow.export(project["id"])
        states = self.store.get(project["id"])["steps"]
        stopped = next((step for step in STEP_IDS if states[step]["state"] not in FINISHED), None)
        report = self.workflow.report(project["id"]) or {}
        record = {"trial": TRIAL_VERSION, "id": trial_id, "created": now(), "label": label, "document": name,
                  "commit": revision, "code": code, "settings": settings, "base": base,
                  "project": project["id"],
                  "failed": {"step": stopped, "message": states[stopped].get("message")} if stopped else None,
                  "steps": [{"id": step["id"], "state": step["state"], "duration": step["duration"],
                             "shared": step["id"] in shared} for step in report.get("steps", [])],
                  "ai": (report.get("ai") or {}).get("totals")}
        record.update(self._score(project["id"], name))
        write_json_atomic(self.trial_dir / f"{trial_id}.json", record)
        return record

    def _base(self, name: str, settings: dict, code: str, steps: tuple[str, ...]) -> str:
        """The id of the document's base for this code and these local settings, run now if there is none."""
        local = {key: settings[key] for key in LOCAL_SETTINGS}
        source = hashlib.sha256((self.documents / name / "source.pdf").read_bytes()).hexdigest()
        key = hashlib.sha256(json.dumps([name, source, code, local], sort_keys=True).encode()).hexdigest()[:16]
        for project in self.store.list():
            if project.get("benchmark", {}).get("base") == key and self._finished(project["id"], steps):
                return project["id"]
        shipped = implementations()
        workflow = Workflow(self.store, steps={step: shipped[step] for step in steps})
        project = workflow.create(self.documents / name / "source.pdf", name=f"{name} · základ",
                                  file_name=f"{name}.pdf", settings=local)
        self.store.mutate(project["id"], lambda doc: doc.update(benchmark={"base": key, "code": code}))
        workflow.run(project["id"])
        if not self._finished(project["id"], steps):
            raise RuntimeError(f"The local steps of {name} did not finish; see project {project['id']}.")
        return project["id"]

    def _finished(self, project_id: str, steps) -> bool:
        states = self.store.get(project_id)["steps"]
        return all(states[step]["state"] in FINISHED for step in steps)

    def _score(self, project_id: str, name: str) -> dict:
        """The score of a trial's export, {"review", "corrections", "totals", "pages", "findings"}; without an
        export (a failed trial) the result is empty and every reference word is missing."""
        plan = self.exported_words(project_id)
        queue = self.workflow.review_queue(project_id) if plan else None
        listed = {(item["page"], word_id) for item in queue["items"] for word_id in item["ids"]} if queue else set()
        ours = [[{**word, "review": (index, word["id"]) in listed} for word in words] for index, words in enumerate(plan)]
        reference, corrections = self.reference(name)
        return {"review": queue and queue["counts"], "corrections": corrections,
                **evaluation.compare(ours, reference)}

    def exported_words(self, project_id: str) -> list[list[dict]]:
        """The words of a project's export {"id", "text", "bbox"}, page by page in reading order; [] without one."""
        result = self.workflow.result(project_id, "export")
        if result is None or not result.exists(PLAN_FILE):
            return []
        return [[word for line in page["lines"] for word in line] for page in result.read_json(PLAN_FILE)["pages"]]

    def crops(self, record: dict, findings: list[dict], out: Path) -> list[Path]:
        """PNG crops of the scan around a trial's findings: the reference box green, our boxes red."""
        out.mkdir(parents=True, exist_ok=True)
        boxes = {(index + 1, word["id"]): word["bbox"]
                 for index, words in enumerate(self.exported_words(record["project"])) for word in words}
        written = []
        for number, finding in enumerate(findings, 1):
            # A fresh copy each time, so that no crop shows the boxes drawn for another.
            with pymupdf.open(self.store.base_pdf(record["project"])) as document:
                page = document[finding["page"] - 1]
                ours = [boxes[(finding["page"], word_id)] for word_id in finding["ids"]
                        if (finding["page"], word_id) in boxes]
                area = pymupdf.Rect(finding["bbox"])
                for box in ours:
                    area |= pymupdf.Rect(box)
                clip = pymupdf.Rect(area.x0 - 60, area.y0 - 25, area.x1 + 60, area.y1 + 25) & page.rect
                page.draw_rect(pymupdf.Rect(finding["bbox"]), color=(0, 0.6, 0), width=0.6)
                for box in ours:
                    page.draw_rect(pymupdf.Rect(box), color=(0.9, 0, 0), width=0.6)
                path = out / f"{number:03d}-p{finding['page']}-{finding['kind']}.png"
                page.get_pixmap(dpi=200, clip=clip).save(path)
                written.append(path)
        return written


def describe(settings: dict) -> str:
    """A configuration in a few words: provider, model and effort."""
    return " ".join(str(part) for part in (settings["ai_provider"], settings["ai_model"] or "výchozí model",
                                           settings["ai_effort"]))


# Command line: python -m ocrmaker.benchmark <command> --------------------------------------------------

# Finding kinds summed in the "geometry" column; missing text counts as silent (GLOSSARY: Silent error).
GEOMETRY = ("extra", "split", "merged", "tangled", "box")


def summary(record: dict) -> dict:
    """The columns of a trial in the trial table."""
    totals, ai = record["totals"], record.get("ai") or {}
    usage = ai.get("usage") or {}
    return {"trial": record["id"][:22], "document": record["document"],
            "config": describe(record["settings"]), "label": record["label"] or "", "commit": record["commit"] or "",
            "failed": (record.get("failed") or {}).get("step") or "",
            "correct %": round(100 * totals["correct"] / max(1, totals["reference_words"]), 2),
            "silent": totals.get("silent", 0) + totals.get("missing", 0),
            "geometry": sum(totals.get(kind, 0) for kind in GEOMETRY),
            "order": f"{totals.get('order', 0)}/{totals.get('order_words', 0)}w",
            "review": record["review"]["markers"] + record["review"]["doubtful"] if record["review"] else "",
            "unnecessary": totals.get("unnecessary", 0),
            "ai s": round(ai.get("duration") or 0), "tokens": usage.get("total")}


def table(rows: list[dict]) -> str:
    if not rows:
        return "(none)"
    columns = list(dict.fromkeys(column for row in rows for column in row))
    widths = {c: max(len(c), *(len(str(row.get(c, ""))) for row in rows)) for c in columns}
    lines = ["  ".join(c.ljust(widths[c]) for c in columns)]
    lines += ["  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns) for row in rows]
    return "\n".join(lines)


def selected(record: dict, kind: str | None, page: int | None) -> list[dict]:
    return [finding for finding in record["findings"]
            if (kind is None or finding["kind"] == kind) and (page is None or finding["page"] == page)]


def main(argv: list[str] | None = None) -> None:
    import argparse

    from . import providers
    from .app import data_directory

    parser = argparse.ArgumentParser(prog="python -m ocrmaker.benchmark", description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, help="benchmark directory (default: <data dir>/benchmark)")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="add a benchmark document: a scan and its reference transcription")
    add.add_argument("name")
    add.add_argument("source", type=Path)
    add.add_argument("reference", type=Path)
    run = commands.add_parser("run", help="run a trial on every (or the given) document")
    run.add_argument("--provider", default="claude")
    run.add_argument("--model")
    run.add_argument("--effort", default="medium")
    run.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                     help='another project setting, e.g. --set reconstruction_accept_confidence=70')
    run.add_argument("--document", action="append")
    run.add_argument("--label")
    listing = commands.add_parser("list", help="the trial table")
    listing.add_argument("--last", type=int, default=20)
    listing.add_argument("--document")
    for name, text in (("show", "a trial's totals per page and its findings"),
                       ("crops", "PNG crops of a trial's findings, for judging them by eye")):
        command = commands.add_parser(name, help=text)
        command.add_argument("trial", help="trial id (a unique prefix will do)")
        command.add_argument("--kind")
        command.add_argument("--page", type=int)
        command.add_argument("--limit", type=int, default=40)
        if name == "crops":
            command.add_argument("--out", type=Path, required=True)
    rescore = commands.add_parser("rescore", help="score trials again after the reference corrections changed")
    rescore.add_argument("trial", nargs="+")
    args = parser.parse_args(argv)

    benchmark = Benchmark(args.root or data_directory(None) / "benchmark", language=providers.available())

    def find(prefix: str) -> dict:
        matches = [trial for trial in benchmark.trials() if trial["id"].startswith(prefix)]
        if len(matches) != 1:
            parser.error(f"trial {prefix!r} matches {len(matches)} trials")
        return matches[0]

    if args.command == "add":
        benchmark.add_document(args.name, args.source.expanduser(), args.reference.expanduser())
        print(f"Added {args.name}.")
    elif args.command == "run":
        changes = {"ai_provider": args.provider, "ai_model": args.model, "ai_effort": args.effort}
        for item in args.set:
            key, _, value = item.partition("=")
            changes[key] = json.loads(value)
        print(table([summary(record) for record in benchmark.run(changes, documents=args.document,
                                                                   label=args.label)]))
    elif args.command == "list":
        trials = [t for t in benchmark.trials() if args.document in (None, t["document"])][-args.last:]
        print(table([summary(trial) for trial in trials]))
    elif args.command == "show":
        record = find(args.trial)
        print(table([summary(record)]), "\n")
        print(table([{"page": page["index"] + 1, **page["totals"]} for page in record["pages"]]), "\n")
        findings = selected(record, args.kind, args.page)
        for finding in findings[:args.limit]:
            print(f"p{finding['page']} {finding['kind']:<11} ref={finding['reference']!r} ours={finding['ours']!r}"
                  f"{' [review]' if finding['review'] else ''} ids={','.join(finding['ids'])}")
        if len(findings) > args.limit:
            print(f"... {len(findings) - args.limit} more")
    elif args.command == "crops":
        record = find(args.trial)
        for path in benchmark.crops(record, selected(record, args.kind, args.page)[:args.limit], args.out):
            print(path)
    elif args.command == "rescore":
        print(table([summary(benchmark.rescore(find(prefix)["id"])) for prefix in args.trial]))


if __name__ == "__main__":
    main()
