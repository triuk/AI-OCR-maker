"""Seam 2: the loopback, token-bound HTTP API and the static Czech WebUI over the project workflow."""
from __future__ import annotations

import secrets
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import settings as project_settings
from .errors import ConfirmationRequired, Conflict
from .reconstruction import MARKER
from .steps import STEPS
from .workflow import Workflow

TOKEN_HEADER = "X-Ocrmaker-Token"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "testserver")
UI = Path(__file__).parent / "ui"


def create_api(workflow: Workflow, *, token: str | None = None, control=None) -> FastAPI:
    """The application; token is the session token (new per process unless a test fixes it).

    control, when given, offers `shutdown()` for the power button of the WebUI.
    """
    app = FastAPI(title="AI-OCR-maker", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token or secrets.token_urlsafe(32)
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", TOKEN_HEADER])

    @app.middleware("http")
    async def protect(request: Request, call_next):
        # The Host check stops DNS rebinding; the token stops other local pages and processes.
        if request.url.hostname not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Neplatný lokální host."}, status_code=403)
        if request.url.path.startswith("/api/") and request.method != "OPTIONS":
            # Images in <img> cannot send headers, so the token may also come as ?token=.
            supplied = request.headers.get(TOKEN_HEADER, "") or request.query_params.get("token", "")
            if not secrets.compare_digest(supplied, app.state.token):
                return JSONResponse({"detail": "Neplatné připojení aplikace. Obnovte stránku."}, status_code=401)
        response = await call_next(request)
        # Without it, browsers keep serving stale UI files after an update; ETags keep revalidation cheap.
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.exception_handler(ConfirmationRequired)
    async def confirmation(request, exc: ConfirmationRequired):
        return JSONResponse({"detail": str(exc), "corrections": exc.corrections}, status_code=409)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": exc.args[0] if exc.args else "Nenalezeno."}, status_code=404)

    @app.get("/bootstrap.json")
    def bootstrap():
        # Not token-protected: the page reads its token here, so the browser URL never carries it.
        # The illegible marker comes from here too, so the UI never spells it itself.
        return {"token": app.state.token, "marker": MARKER}

    @app.get("/api/projects")
    def projects():
        return {"projects": workflow.list(),
                "steps": [{"id": d.id, "label": d.label, "manual": d.manual,
                           "preview": (UI / "previews" / f"{d.id}.js").is_file()} for d in STEPS],
                "choices": project_settings.choices()}

    @app.post("/api/projects", status_code=201)
    def create(source: UploadFile = File(...), name: str = Form(""), language: str = Form("")):
        uploads = workflow.store.root / ".uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=uploads, suffix=".pdf") as staged:
            shutil.copyfileobj(source.file, staged, 1 << 20)
            staged.flush()
            return workflow.create(Path(staged.name), name=name, file_name=source.filename or "dokument.pdf",
                                   settings={"language": language} if language else None)

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return workflow.get(project_id)

    @app.delete("/api/projects/{project_id}")
    def delete(project_id: str):
        workflow.delete(project_id)
        return {"deleted": project_id}

    @app.get("/api/projects/{project_id}/settings")
    def get_settings(project_id: str):
        return workflow.get(project_id)["settings"]

    @app.put("/api/projects/{project_id}/settings")
    def put_settings(project_id: str, changes: dict):
        return workflow.update_settings(project_id, changes)

    @app.post("/api/projects/{project_id}/ai-check")
    def ai_check(project_id: str, changes: dict | None = None):
        # A failed check is a valid answer ({"ok": false, "message": Czech}), not an HTTP error.
        return workflow.check_ai(project_id, changes)

    @app.post("/api/projects/{project_id}/run", status_code=202)
    def run(project_id: str):
        return workflow.start(project_id, "run")

    @app.post("/api/projects/{project_id}/text-layer/remove", status_code=202)
    def remove_text_layer(project_id: str):
        """The choice Import's stop offers for an earlier OCR's invisible text layer; visible text gets 422."""
        return workflow.start(project_id, "remove_text_layer")

    @app.post("/api/projects/{project_id}/export", status_code=202)
    def export(project_id: str):
        return workflow.start(project_id, "export")

    @app.post("/api/projects/{project_id}/stop")
    def stop(project_id: str):
        workflow.get(project_id)
        return {"stopping": workflow.stop(project_id)}

    @app.get("/api/projects/{project_id}/steps/{step_id}/rerun")
    def rerun_impact(project_id: str, step_id: str):
        return workflow.rerun_impact(project_id, step_id)

    @app.post("/api/projects/{project_id}/steps/{step_id}/rerun", status_code=202)
    def rerun(project_id: str, step_id: str, body: dict | None = None):
        confirmed = (body or {}).get("discard_corrections")
        return workflow.start(project_id, "rerun", step_id=step_id, discard_corrections=confirmed)

    @app.get("/api/projects/{project_id}/steps/{step_id}/overlay/{page}")
    def overlay(project_id: str, step_id: str, page: int):
        data = workflow.overlay(project_id, step_id, page)
        if data is None:
            raise KeyError("Krok pro tuto stránku nemá náhled.")
        return data

    @app.get("/api/projects/{project_id}/pages")
    def pages(project_id: str):
        """Page geometry (ocrmaker.pages) every step preview needs; empty until Import has finished."""
        return {"pages": workflow.pages(project_id)}

    @app.get("/api/projects/{project_id}/pages/{page}/image")
    def page_image(project_id: str, page: int):
        return FileResponse(workflow.page_image(project_id, page), media_type="image/png")

    @app.get("/api/projects/{project_id}/review/queue")
    def review_queue(project_id: str, reconstructions: bool = False):
        """Manual review's queue of illegible markers (ocrmaker.review), with ?reconstructions=true followed by
        the accepted reconstructions; 422 until automation has finished."""
        return workflow.review_queue(project_id, reconstructions=reconstructions)

    @app.get("/api/projects/{project_id}/review/pages/{page}")
    def review_page(project_id: str, page: int):
        """A page's text as Export would write it now, word by word with origins and correction targets."""
        return workflow.review_page(project_id, page)

    @app.get("/api/projects/{project_id}/review/pages/{page}/crop/{word_id}")
    def review_crop(project_id: str, page: int, word_id: str):
        return Response(workflow.review_crop(project_id, page, word_id), media_type="image/png")

    @app.get("/api/projects/{project_id}/corrections")
    def corrections(project_id: str):
        return {"corrections": workflow.corrections(project_id)}

    @app.put("/api/projects/{project_id}/corrections")
    def correct(project_id: str, body: dict):
        """Save a manual correction at once: {"page", "word" (a written word's id), "text", "source"}."""
        if not isinstance(body.get("page"), int) or not isinstance(body.get("word"), str) \
                or not isinstance(body.get("text"), str):
            raise ValueError("Oprava potřebuje stránku, slovo a text.")
        correction = workflow.correct(project_id, body["page"], body["word"], body["text"],
                                      source=body.get("source") or "word")
        # The open markers that now offer the same text (propagation), for a one-click acceptance.
        suggestions = workflow.suggestions(project_id, correction["id"]) if correction else []
        return {"correction": correction, "suggestions": suggestions}

    @app.get("/api/projects/{project_id}/review/suggestions")
    def suggestions(project_id: str):
        """The open markers carrying a propagation suggestion, in queue order."""
        return {"suggestions": workflow.suggestions(project_id)}

    @app.post("/api/projects/{project_id}/review/suggestions")
    def accept_suggestion(project_id: str, body: dict):
        """Accept a queue item's suggestion: {"page", "word" (the item's id)}; 404 when it has none."""
        if not isinstance(body.get("page"), int) or not isinstance(body.get("word"), str):
            raise ValueError("Přijetí návrhu potřebuje stránku a slovo.")
        return {"correction": workflow.accept_suggestion(project_id, body["page"], body["word"])}

    @app.put("/api/projects/{project_id}/review/pages/{page}/order")
    def reorder_articles(project_id: str, page: int, body: dict):
        """Save a page's manual article order: {"articles": [every article id of the page, in the new order]}."""
        return {"correction": workflow.reorder_articles(project_id, page, body.get("articles"))}

    @app.post("/api/projects/{project_id}/review/notes")
    def add_note(project_id: str, body: dict):
        """Attach a region note: {"page", "bbox" [x0, y0, x1, y1] in source page coordinates, "text"}."""
        if not isinstance(body.get("page"), int):
            raise ValueError("Poznámka potřebuje stránku.")
        return {"note": workflow.add_note(project_id, body["page"], body.get("bbox"), body.get("text"))}

    @app.put("/api/projects/{project_id}/review/notes/{note_id}")
    def edit_note(project_id: str, note_id: str, body: dict):
        """Change a region note's text: {"text"}. A note is removed like any correction (DELETE corrections)."""
        return {"note": workflow.edit_note(project_id, note_id, body.get("text"))}

    @app.delete("/api/projects/{project_id}/corrections/{correction_id}")
    def revert_correction(project_id: str, correction_id: str):
        workflow.revert_correction(project_id, correction_id)
        return {"reverted": correction_id}

    @app.get("/api/projects/{project_id}/outputs")
    def outputs(project_id: str):
        """The files of the last export (name, size); empty until Export has finished."""
        return {"outputs": workflow.outputs(project_id)}

    @app.get("/api/projects/{project_id}/outputs/{name}")
    def output(project_id: str, name: str):
        """Download one exported file (also with ?token= for a plain link)."""
        path = workflow.output(project_id, name)
        return FileResponse(path, media_type="application/pdf" if name.endswith(".pdf") else None, filename=name)

    @app.get("/api/projects/{project_id}/reports")
    def reports(project_id: str):
        """The project's run reports, oldest first."""
        return {"reports": workflow.reports(project_id)}

    @app.get("/api/projects/{project_id}/report")
    def report(project_id: str, run_id: str | None = None):
        """A run report as JSON (ocrmaker.report), by default the latest run's."""
        content = workflow.report(project_id, run_id)
        if content is None:
            raise KeyError("Zpráva o běhu zatím neexistuje.")
        return content

    @app.get("/api/projects/{project_id}/report/summary")
    def report_summary(project_id: str, run_id: str | None = None):
        """The human summary of a run report (Markdown shown as plain text; also with ?token= for a link)."""
        summary = workflow.report_summary(project_id, run_id)
        if summary is None:
            raise KeyError("Zpráva o běhu zatím neexistuje.")
        return PlainTextResponse(summary)

    @app.get("/api/projects/{project_id}/diagnostics")
    def diagnostics(project_id: str, run_id: str | None = None):
        return {"records": workflow.diagnostics(project_id, run_id)}

    @app.post("/api/application/shutdown")
    def shutdown():
        if control is None:
            raise KeyError("Ovládání aplikace není dostupné.")
        control.shutdown()
        return {"accepted": True}

    # Must stay last: it catches every path not matched above.
    app.mount("/", StaticFiles(directory=UI, html=True), name="ui")
    return app
