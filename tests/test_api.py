"""Seam 2: the HTTP API the WebUI uses, through FastAPI's TestClient."""
from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import TOKEN
from fakes import BlockingStep, RecordingStep
from ocrmaker.api import create_api
from ocrmaker.store import ProjectStore
from ocrmaker.workflow import Workflow


def upload(client, path, name=None, language=None):
    data = {key: value for key, value in {"name": name, "language": language}.items() if value}
    with path.open("rb") as source:
        return client.post("/api/projects", data=data, files={"source": (path.name, source, "application/pdf")})


def api_for(library, *steps):
    workflow = Workflow(ProjectStore(library), steps={step.id: step for step in steps})
    return workflow, TestClient(create_api(workflow, token=TOKEN), headers={"X-Ocrmaker-Token": TOKEN})


def test_the_api_requires_the_session_token(workflow):
    anonymous = TestClient(create_api(workflow, token=TOKEN))

    refused = anonymous.get("/api/projects")
    allowed = anonymous.get("/api/projects", headers={"X-Ocrmaker-Token": TOKEN})
    by_query = anonymous.get(f"/api/projects?token={TOKEN}")

    assert refused.status_code == 401
    assert refused.json()["detail"] == "Neplatné připojení aplikace. Obnovte stránku."
    assert (allowed.status_code, by_query.status_code) == (200, 200)


def test_the_page_gets_its_session_token_from_bootstrap_and_is_czech(workflow):
    anonymous = TestClient(create_api(workflow, token=TOKEN))

    assert anonymous.get("/bootstrap.json").json()["token"] == TOKEN
    page = anonymous.get("/")
    assert page.status_code == 200
    assert '<html lang="cs">' in page.text


def test_requests_for_a_foreign_host_name_are_refused(workflow):
    rebinding = TestClient(create_api(workflow, token=TOKEN), base_url="http://attacker.example")

    assert rebinding.get("/bootstrap.json").status_code == 403


def test_a_project_is_created_from_an_uploaded_pdf_listed_and_opened(client, source_pdf):
    created = upload(client, source_pdf, name="Zpravodaj 5/2000")

    assert created.status_code == 201
    project = created.json()
    listing = client.get("/api/projects").json()
    assert [(p["id"], p["name"]) for p in listing["projects"]] == [(project["id"], "Zpravodaj 5/2000")]
    opened = client.get(f"/api/projects/{project['id']}").json()
    assert opened["source"]["file_name"] == "zpravodaj-2000-05.pdf"
    assert [step["state"] for step in opened["steps"]] == ["pending"] * 10


def test_the_listing_describes_the_ten_workflow_steps_and_the_languages(client):
    listing = client.get("/api/projects").json()

    assert [step["label"] for step in listing["steps"]] == [
        "Import", "Příprava obrazu", "Lokální OCR", "Rozvržení", "Pořadí čtení", "Detekce problémů",
        "AI rekonstrukce", "Dohledání na webu", "Ruční kontrola", "Export"]
    assert {"id": "ces", "label": "čeština"} in listing["choices"]["language"]


def test_projects_persist_across_an_application_restart(client, library, source_pdf):
    project = upload(client, source_pdf).json()

    _, restarted = api_for(library)

    assert [p["id"] for p in restarted.get("/api/projects").json()["projects"]] == [project["id"]]


def test_a_deleted_project_disappears(client, library, source_pdf):
    project = upload(client, source_pdf).json()

    assert client.delete(f"/api/projects/{project['id']}").status_code == 200

    assert client.get("/api/projects").json()["projects"] == []
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    _, restarted = api_for(library)
    assert restarted.get("/api/projects").json()["projects"] == []


def test_an_upload_that_is_not_a_pdf_is_refused_in_czech(client, tmp_path):
    fake = tmp_path / "scan.pdf"
    fake.write_bytes(b"plain text")

    refused = upload(client, fake)

    assert refused.status_code == 422
    assert refused.json()["detail"] == "Soubor není čitelné PDF."
    assert client.get("/api/projects").json()["projects"] == []


def test_a_project_can_be_created_with_another_document_language(client, source_pdf):
    project = upload(client, source_pdf, language="pol").json()

    assert client.get(f"/api/projects/{project['id']}/settings").json()["language"] == "pol"


def test_project_settings_offer_the_document_language(client, source_pdf):
    project = upload(client, source_pdf).json()
    settings = f"/api/projects/{project['id']}/settings"

    assert client.get(settings).json()["language"] == "ces"
    assert client.put(settings, json={"language": "deu"}).json()["settings"]["language"] == "deu"
    refused = client.put(settings, json={"language": "klingon"})
    assert (refused.status_code, refused.json()["detail"]) == (422, "Neznámý jazyk dokumentu.")


def test_run_is_refused_while_the_workflow_has_no_steps(library, source_pdf):
    _, client = api_for(library)
    project = upload(client, source_pdf).json()

    refused = client.post(f"/api/projects/{project['id']}/run")

    assert refused.status_code == 422
    assert "krok" in refused.json()["detail"]


def test_a_second_run_and_deletion_are_refused_while_a_run_is_in_progress(library, source_pdf):
    blocking = BlockingStep("import")
    workflow, client = api_for(library, blocking)
    project = upload(client, source_pdf).json()

    assert client.post(f"/api/projects/{project['id']}/run").status_code == 202
    assert blocking.entered.wait(5)
    second = client.post(f"/api/projects/{project['id']}/run")
    deletion = client.delete(f"/api/projects/{project['id']}")
    running = client.get(f"/api/projects/{project['id']}").json()

    assert (second.status_code, deletion.status_code) == (409, 409)
    assert second.json()["detail"] == "Projekt se právě zpracovává. Nejprve zpracování zastavte."
    assert running["running"] is True
    assert running["steps"][0]["state"] == "running"
    blocking.release.set()
    workflow.wait(project["id"], timeout=5)
    assert client.get(f"/api/projects/{project['id']}").json()["steps"][0]["state"] == "done"


def test_a_run_can_be_stopped(library, source_pdf):
    blocking = BlockingStep("import")
    workflow, client = api_for(library, blocking)
    project = upload(client, source_pdf).json()
    client.post(f"/api/projects/{project['id']}/run")
    assert blocking.entered.wait(5)

    assert client.post(f"/api/projects/{project['id']}/stop").json() == {"stopping": True}
    workflow.wait(project["id"], timeout=5)

    stopped = client.get(f"/api/projects/{project['id']}").json()
    assert (stopped["running"], stopped["steps"][0]["state"]) == (False, "pending")


def test_step_preview_overlay_data_is_served_per_page(library, source_pdf):
    workflow, client = api_for(library, RecordingStep("import", overlay={"page": {"width": 595}}))
    project = upload(client, source_pdf).json()
    client.post(f"/api/projects/{project['id']}/run")
    workflow.wait(project["id"], timeout=5)
    base = f"/api/projects/{project['id']}/steps"

    assert client.get(f"{base}/import/overlay/0").json() == {"page": {"width": 595}}
    assert client.get(f"{base}/import/overlay/1").status_code == 404
    assert client.get(f"{base}/layout/overlay/0").status_code == 404
    assert client.get(f"{base}/nonsense/overlay/0").status_code == 404


def test_a_rerun_reports_the_manual_corrections_it_would_discard_and_needs_confirmation(library, source_pdf):
    imported = RecordingStep("import")
    workflow, client = api_for(library, imported, RecordingStep("review"))
    project = upload(client, source_pdf).json()
    workflow.run(project["id"])
    ProjectStore(library).write_corrections(project["id"], [{"word": "p1-w3", "text": "Rychvald"}])
    rerun = f"/api/projects/{project['id']}/steps/import/rerun"

    assert client.get(rerun).json()["corrections"] == 1
    refused = client.post(rerun, json={})
    assert refused.status_code == 409
    assert refused.json() == {"detail": "Opakování kroku zahodí 1 ruční opravu.", "corrections": 1}

    assert client.post(rerun, json={"discard_corrections": 1}).status_code == 202
    workflow.wait(project["id"], timeout=5)
    assert imported.calls == 2
    assert client.get(rerun).json()["corrections"] == 0


def test_diagnostics_list_the_step_records(library, source_pdf):
    workflow, client = api_for(library, RecordingStep("import"))
    project = upload(client, source_pdf).json()
    client.post(f"/api/projects/{project['id']}/run")
    workflow.wait(project["id"], timeout=5)

    records = client.get(f"/api/projects/{project['id']}/diagnostics").json()["records"]

    assert [(r["kind"], r["step"]) for r in records] == [("step_started", "import"), ("step", "import")]
    assert records[1]["outcome"] == "done"


def test_imported_pages_and_their_working_rasters_are_served_for_step_previews(client, workflow, source_pdf):
    project = upload(client, source_pdf).json()
    base = f"/api/projects/{project['id']}"
    assert client.get(f"{base}/pages").json() == {"pages": []}
    assert client.get(f"{base}/pages/0/image").status_code == 404

    client.post(f"{base}/run")
    workflow.wait(project["id"])

    pages = client.get(f"{base}/pages").json()["pages"]
    assert [(page["index"], page["width"], page["height"]) for page in pages] == [(0, 595, 842), (1, 595, 842)]
    image = client.get(f"{base}/pages/1/image")
    assert (image.status_code, image.headers["content-type"]) == (200, "image/png")
    assert image.content.startswith(b"\x89PNG")
    assert client.get(f"{base}/pages/2/image").status_code == 404
    assert client.get(f"{base}/steps/import/overlay/0").json()["bounds"] == [0, 0, 595, 842]
    assert client.get(f"/api/projects/{project['id']}/pages/0/image",
                      headers={"X-Ocrmaker-Token": ""}, params={"token": TOKEN}).status_code == 200


def test_the_listing_says_which_steps_have_a_preview_and_the_ui_serves_it(client):
    listing = client.get("/api/projects").json()

    previews = {step["id"]: step["preview"] for step in listing["steps"]}
    assert previews["import"] is True and previews["review"] is False
    assert client.get("/previews/import.js").status_code == 200
    assert "pageViewer" in client.get("/viewer.js").text


def test_the_exported_ocr_pdf_is_listed_and_downloaded_under_a_name_derived_from_the_source(client, workflow,
                                                                                         source_pdf):
    project = upload(client, source_pdf).json()
    base = f"/api/projects/{project['id']}"
    assert client.get(f"{base}/outputs").json() == {"outputs": []}

    client.post(f"{base}/run")
    workflow.wait(project["id"])

    [output] = client.get(f"{base}/outputs").json()["outputs"]
    assert output["name"] == "zpravodaj-2000-05-ocr.pdf"
    download = client.get(f"{base}/outputs/zpravodaj-2000-05-ocr.pdf")
    assert (download.status_code, download.headers["content-type"]) == (200, "application/pdf")
    assert 'filename="zpravodaj-2000-05-ocr.pdf"' in download.headers["content-disposition"]
    assert download.content.startswith(b"%PDF") and len(download.content) == output["size"]
    assert client.get(f"{base}/outputs/source.pdf").status_code == 404
    assert client.get(f"{base}/outputs/zpravodaj-2000-05-ocr.pdf",
                      headers={"X-Ocrmaker-Token": ""}, params={"token": TOKEN}).status_code == 200


def test_the_listing_offers_previews_of_local_ocr_and_export(client):
    previews = {step["id"]: step["preview"] for step in client.get("/api/projects").json()["steps"]}

    assert previews["ocr"] is True and previews["export"] is True
    assert "confidence" in client.get("/previews/ocr.js").text


def test_project_settings_offer_claude_or_codex_and_their_models(client, source_pdf):
    project = upload(client, source_pdf).json()
    choices = client.get("/api/projects").json()["choices"]
    assert [provider["id"] for provider in choices["ai_provider"]] == ["claude", "codex"]
    assert {"id": "opus", "label": "Opus"} in choices["ai_model"]["claude"]
    assert choices["ai_model"]["codex"]

    settings = f"/api/projects/{project['id']}/settings"
    saved = client.put(settings, json={"ai_provider": "claude", "ai_model": "opus"}).json()["settings"]
    assert (saved["ai_provider"], saved["ai_model"]) == ("claude", "opus")
    refused = client.put(settings, json={"ai_model": "gpt-unknown"})
    assert refused.status_code == 422
    assert refused.json()["detail"] == "Model „gpt-unknown“ není u poskytovatele Claude k dispozici."


def test_check_ai_shows_who_answered_or_why_not(client, source_pdf, language_provider):
    from ocrmaker.language import LanguageError

    project = upload(client, source_pdf).json()
    check = f"/api/projects/{project['id']}/ai-check"
    language_provider.on("check", {"ok": True}, LanguageError("Limit předplatného Claude je vyčerpaný.",
                                                               code="usage_limit"))

    answered = client.post(check, json={"ai_model": "haiku"}).json()
    limited = client.post(check).json()

    assert answered | {"ok": True, "provider": "claude", "model": "haiku"} == answered
    assert answered["message"] == "Claude odpověděl (model haiku)."
    assert limited | {"ok": False, "code": "usage_limit", "message": "Limit předplatného Claude je vyčerpaný."} == limited
    records = client.get(f"/api/projects/{project['id']}/diagnostics").json()["records"]
    assert [r["outcome"] for r in records if r["kind"] == "ai_request"] == ["ok", "failed"]
