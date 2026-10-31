"""Project store: one directory per project, written atomically; the source PDF copy is never modified.

Layout of `<root>/projects/<id>/`:
    project.json          the project file (name, source facts, settings, step states)
    source.pdf            a read-only copy of the user's source PDF
    working.pdf           the clean working copy, once an earlier OCR's text layer was removed (read-only)
    steps/<step id>/      the committed result of each step (see ocrmaker.step.StepResult)
    corrections.json      manual corrections, a separate layer applied at export (ADR 0004; ocrmaker.corrections)
    diagnostics/<run>.jsonl  diagnostic records of one workflow run
    .run.lock             held while a workflow run owns the project
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .files import read_json, write_json_atomic
from .locking import FileLock

PROJECT_FILE = "project.json"
SOURCE_FILE = "source.pdf"
WORKING_FILE = "working.pdf"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ProjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.projects = self.root / "projects"
        self.projects.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._recover()

    def _recover(self) -> None:
        # Leftovers of a crash during create, delete or a step commit are never valid projects or results.
        for leftover in self.projects.glob(".*"):
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
        for leftover in self.projects.glob("*/steps/.*"):
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
        for leftover in self.projects.glob("*/.working-*"):
            leftover.unlink(missing_ok=True)

    # Paths -------------------------------------------------------------------------------------------

    def directory(self, project_id: str) -> Path:
        # The id becomes a path, so only the form create() generates is accepted.
        if not (isinstance(project_id, str) and len(project_id) == 32 and project_id.isalnum()
                and project_id.isascii() and project_id == project_id.lower()):
            raise KeyError("Projekt neexistuje.")
        return self.projects / project_id

    def source_pdf(self, project_id: str) -> Path:
        return self.directory(project_id) / SOURCE_FILE

    def base_pdf(self, project_id: str) -> Path:
        """The PDF the workflow works from: the clean working copy once there is one, else the source copy."""
        working = self.directory(project_id) / WORKING_FILE
        return working if working.is_file() else self.source_pdf(project_id)

    def write_working_pdf(self, project_id: str, write: Callable[[Path], object]):
        """Create the clean working copy: write(path) fills a staged file that then replaces it atomically."""
        directory = self.directory(project_id)
        staged = directory / f".working-{uuid.uuid4().hex}.pdf"
        try:
            outcome = write(staged)
            with staged.open("rb") as written:
                os.fsync(written.fileno())
            staged.chmod(stat.S_IRUSR | stat.S_IRGRP)
            staged.replace(directory / WORKING_FILE)
        finally:
            staged.unlink(missing_ok=True)
        return outcome

    def run_lock(self, project_id: str) -> FileLock:
        return FileLock(self.directory(project_id) / ".run.lock", "Projekt právě zpracovává jiný běh.")

    def diagnostics_dir(self, project_id: str) -> Path:
        return self.directory(project_id) / "diagnostics"

    # Documents ---------------------------------------------------------------------------------------

    def create(self, source: Path, project: dict) -> dict:
        """Copy the source PDF into a new project and write its project file; both appear at once or not at all."""
        project_id = uuid.uuid4().hex
        staging = self.projects / f".new-{project_id}"
        staging.mkdir()
        try:
            copy = staging / SOURCE_FILE
            digest = hashlib.sha256()
            with Path(source).open("rb") as reader, copy.open("wb") as writer:
                for chunk in iter(lambda: reader.read(1 << 20), b""):
                    digest.update(chunk)
                    writer.write(chunk)
            copy.chmod(stat.S_IRUSR | stat.S_IRGRP)
            created = now()
            project = {**project, "id": project_id, "created": created, "updated": created, "version": 1}
            project["source"] = {**project.get("source", {}), "size": copy.stat().st_size,
                                  "sha256": digest.hexdigest()}
            write_json_atomic(staging / PROJECT_FILE, project)
            staging.rename(self.directory(project_id))
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return project

    def get(self, project_id: str) -> dict:
        path = self.directory(project_id) / PROJECT_FILE
        try:
            return read_json(path)
        except FileNotFoundError:
            raise KeyError("Projekt neexistuje.") from None

    def list(self) -> list[dict]:
        projects = []
        for directory in self.projects.iterdir():
            if directory.name.startswith(".") or not (directory / PROJECT_FILE).is_file():
                continue
            try:
                projects.append(self.get(directory.name))
            except (KeyError, ValueError):
                continue
        return sorted(projects, key=lambda d: d["created"], reverse=True)

    def mutate(self, project_id: str, change: Callable[[dict], None]) -> dict:
        """The single write path of a project file: load, change in place, bump the version, write."""
        with self._lock:
            project = self.get(project_id)
            change(project)
            project["version"] += 1
            project["updated"] = now()
            write_json_atomic(self.directory(project_id) / PROJECT_FILE, project)
            return project

    def delete(self, project_id: str) -> None:
        """Remove a project; the caller holds its run lock. A rename first makes it vanish from the list at once."""
        directory = self.directory(project_id)
        if not (directory / PROJECT_FILE).is_file():
            raise KeyError("Projekt neexistuje.")
        trash = self.projects / f".deleted-{project_id}"
        directory.rename(trash)
        shutil.rmtree(trash, ignore_errors=True)

    # Step results ------------------------------------------------------------------------------------

    def result_dir(self, project_id: str, step_id: str) -> Path:
        return self.directory(project_id) / "steps" / step_id

    def new_result_dir(self, project_id: str, step_id: str) -> Path:
        """An empty staging directory for a running step's result."""
        staging = self.directory(project_id) / "steps" / f".{step_id}-{uuid.uuid4().hex}"
        staging.mkdir(parents=True)
        return staging

    def commit_result(self, project_id: str, step_id: str, staging: Path) -> None:
        """Replace the step's committed result with the staged one."""
        target = self.result_dir(project_id, step_id)
        old = staging.with_name(f".{step_id}-old-{uuid.uuid4().hex}")
        if target.exists():
            target.rename(old)
        staging.rename(target)
        shutil.rmtree(old, ignore_errors=True)

    def discard_result(self, project_id: str, step_id: str) -> None:
        target = self.result_dir(project_id, step_id)
        if target.exists():
            old = target.with_name(f".{step_id}-old-{uuid.uuid4().hex}")
            target.rename(old)
            shutil.rmtree(old, ignore_errors=True)

    # Manual corrections ------------------------------------------------------------------------------

    def corrections(self, project_id: str) -> list:
        path = self.directory(project_id) / "corrections.json"
        return read_json(path) if path.is_file() else []

    def write_corrections(self, project_id: str, corrections: list) -> None:
        write_json_atomic(self.directory(project_id) / "corrections.json", corrections)
