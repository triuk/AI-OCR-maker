"""Seam 1 and 2: Web lookup settles low-confidence problem spots with short targeted web searches, scripted."""
from __future__ import annotations

from fakes import answer_spots
from ocrmaker.words import page_file
from test_reconstruction import DAMAGED, MARKER, PrintedOcr, exported, ocr_words, run, spot_id, word_index

SOURCE = "https://www.example.cz/zpravodaj/beseda-v-knihovne"

# AI reconstruction leaves "čtvrtek" (read "čtv") and the empty box of "kronikářem" unresolved.
DOUBTFUL = lambda request: answer_spots({  # noqa: E731
    spot_id(request, "opr"): {"text": "opravu", "confidence": 95},
    spot_id(request, "čtv"): {"text": "čtvrtek", "confidence": 40},
})(request)


def found(settle: dict[str, dict]):
    """A scripted lookup answer: the spots whose reading or candidate is a key of settle, with those fields."""
    def answer(request):
        items = []
        for spot in request.data["spots"]:
            fields = settle.get(spot["candidate"]) or settle.get(spot["reading"])
            if fields is not None:
                items.append({"ids": spot["ids"], "words": fields["words"],
                              "confidence": fields.get("confidence", 90),
                              "queries": fields.get("queries", spot["queries"][:1]),
                              "answer": fields.get("answer", "The source quotes the sentence."),
                              "sources": fields.get("sources", [SOURCE]),
                              "reasoning": fields.get("reasoning", "Scripted lookup.")})
        return {"lookups": items}
    return answer


def states(project) -> dict:
    return {step["id"]: step["state"] for step in project["steps"]}


def test_a_spot_the_web_settles_gets_its_text_and_source_url_and_an_unsettled_spot_keeps_the_marker(
        tmp_path, language_provider):
    language_provider.on("reconstruction", DOUBTFUL)
    language_provider.on("lookup", found({"čtvrtek": {"words": ["čtvrtek"]}}))
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    assert states(project)["lookup"] == "done"
    words = [text for text, _ in exported(workflow, project)]
    assert words[word_index("čtvrtek")] == "čtvrtek"
    assert words[word_index("kronikářem")] == MARKER
    assert words[word_index("opravu")] == "opravu"
    [record] = workflow.result(project["id"], "lookup").read_json(page_file(0))["reconstructions"]
    assert record["source"] == "web" and record["sources"] == [SOURCE]
    assert [word["text"] for word in record["words"]] == ["čtvrtek"]
    assert record["queries"] and all("čtvrtek" in query for query in record["queries"])
    [request] = language_provider.requests_for("lookup")
    assert request.web_search is True


QUERY_LIMIT = 60  # characters: a short snippet, never a passage


def test_queries_carry_only_short_snippets_of_the_page(tmp_path, language_provider):
    long_reading = "čtv" + "x" * 70  # a runaway partial reading must not lengthen the query
    readings = {**DAMAGED, word_index("čtvrtek"): (long_reading, 20.0)}
    language_provider.on("reconstruction", lambda request: answer_spots({
        spot_id(request, long_reading): {"text": "čtvrtek", "confidence": 40}})(request))
    language_provider.on("lookup", {"lookups": []})
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=readings))

    [request] = language_provider.requests_for("lookup")
    sent = [query for spot in request.data["spots"] for query in spot["queries"]]
    assert sent and all(0 < len(query) <= QUERY_LIMIT for query in sent)
    for spot in request.data["spots"]:
        assert len(spot["before"].split()) <= 3 and len(spot["after"].split()) <= 3
    sentence = "Knihovna rozšiřuje otevírací dobu a nabízí čtenářům nové tituly"
    assert sentence not in str(request.data)
    assert "beseda s místním" in " ".join(sent)  # the words printed around the empty box of "kronikářem"


def test_a_settlement_reporting_a_long_query_is_not_accepted(tmp_path, language_provider):
    language_provider.on("reconstruction", DOUBTFUL)
    long_query = "Ve čtvrtek proběhne beseda s místním kronikářem o historii hornictví v obci"
    language_provider.on("lookup", found({"čtvrtek": {"words": ["čtvrtek"], "queries": [long_query]}}))
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    assert [text for text, _ in exported(workflow, project)][word_index("čtvrtek")] == MARKER
    lookup = next(step for step in project["steps"] if step["id"] == "lookup")
    assert lookup["state"] == "warning" and "neplatné" in lookup["message"]


def test_with_web_lookup_switched_off_nothing_is_searched_and_low_confidence_spots_get_the_marker(
        tmp_path, language_provider):
    language_provider.on("reconstruction", DOUBTFUL)
    language_provider.on("lookup", found({"čtvrtek": {"words": ["čtvrtek"]}}))
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED), web_lookup=False)

    assert language_provider.requests_for("lookup") == []
    words = [text for text, _ in exported(workflow, project)]
    assert [words[word_index("čtvrtek")], words[word_index("kronikářem")]] == [MARKER, MARKER]
    assert workflow.overlay(project["id"], "lookup", 0)["enabled"] is False
    assert "Web lookup is switched off" in workflow.report_summary(project["id"])


def test_the_preview_and_the_report_show_the_words_the_web_settled(tmp_path, language_provider):
    language_provider.on("reconstruction", DOUBTFUL)
    language_provider.on("lookup", found({"čtvrtek": {"words": ["čtvrtek"], "answer": "Beseda ve čtvrtek."}}))
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    overlay = workflow.overlay(project["id"], "lookup", 0)
    by_status = {word["status"]: word for word in overlay["words"]}
    web = by_status["web"]
    assert (web["text"], web["hint"], web["sources"], web["answer"]) == ("čtvrtek", "čtv", [SOURCE],
                                                                        "Beseda ve čtvrtek.")
    assert web["bbox"] == ocr_words(workflow, project)[web["id"]]["bbox"]  # the spot's own box
    assert web["queries"] and web["confidence"] == 90
    assert by_status["unresolved"]["text"] == MARKER and by_status["unresolved"]["queries"]
    assert overlay["counts"] == {"spots": 2, "searched": 2, "settled": 1, "markers": 1, "kept": 0}

    section = workflow.report(project["id"])["sections"]["lookup"]
    assert section["total"] == {"spots": 2, "searched": 2, "settled": 1, "markers": 1, "kept": 0}
    [settled] = section["settled"]
    assert (settled["words"], settled["hint"], settled["sources"]) == (["čtvrtek"], "čtv", [SOURCE])
    summary = workflow.report_summary(project["id"])
    assert "1 settled by a web source, 1 illegible markers and 0 doubtful readings kept as read remain" in summary
    assert "'čtvrtek'" in summary and SOURCE in summary


def test_a_web_answer_below_the_acceptance_confidence_or_without_a_source_does_not_settle_the_spot(
        tmp_path, language_provider):
    language_provider.on("reconstruction", DOUBTFUL)
    language_provider.on("lookup", found({"čtvrtek": {"words": ["čtvrtek"], "confidence": 50},
                                          "": {"words": ["kronikářem"], "sources": []}}))
    workflow, project = run(tmp_path, language_provider, PrintedOcr(readings=DAMAGED))

    words = [text for text, _ in exported(workflow, project)]
    assert [words[word_index("čtvrtek")], words[word_index("kronikářem")]] == [MARKER, MARKER]
    outcomes = {item["outcome"] for item in
                workflow.result(project["id"], "lookup").read_json(page_file(0))["lookups"]}
    assert outcomes == {"insufficient"}


def test_without_an_ai_provider_web_lookup_warns_and_the_spots_keep_the_marker(tmp_path):
    workflow, project = run(tmp_path, None, PrintedOcr(readings=DAMAGED))

    lookup = next(step for step in project["steps"] if step["id"] == "lookup")
    assert lookup["state"] == "warning" and "AI není k dispozici" in lookup["message"]
    words = [text for text, _ in exported(workflow, project)]
    assert [words[index] for index in DAMAGED] == [MARKER] * 3


def test_the_api_takes_the_web_lookup_switch_and_serves_its_preview(client, workflow, source_pdf):
    project = workflow.create(source_pdf)
    settings = f"/api/projects/{project['id']}/settings"

    assert client.get(settings).json()["web_lookup"] is True
    assert client.put(settings, json={"web_lookup": False}).json()["settings"]["web_lookup"] is False
    assert client.put(settings, json={"web_lookup": "ne"}).status_code == 422
    listing = {step["id"]: step["preview"] for step in client.get("/api/projects").json()["steps"]}
    assert listing["lookup"] is True
    assert "sources" in client.get("/previews/lookup.js").text


def test_a_web_settled_split_gets_word_boxes_measured_inside_the_joined_box(tmp_path, language_provider):
    joined = "místnímkronikářem"
    language_provider.on("reconstruction", lambda request: answer_spots({
        spot_id(request, joined): {"words": ["místním", "kronikářem"], "confidence": 40}})(request))
    language_provider.on("lookup", found({"místním kronikářem": {"words": ["místním", "kronikářem"]}}))
    workflow, project = run(tmp_path, language_provider, PrintedOcr(joined=[word_index("místním")]))

    box = next(word for word in ocr_words(workflow, project).values() if word["text"] == joined)["bbox"]
    written = dict(exported(workflow, project))
    first, second = written["místním"], written["kronikářem"]
    assert first.x1 <= second.x0  # two words, in order
    for rect in (first, second):
        assert box[0] - 1 <= rect.x0 and rect.x1 <= box[2] + 1


def test_a_doubtful_reading_the_web_does_not_settle_keeps_its_printed_text(tmp_path, language_provider):
    language_provider.on("reconstruction", lambda request: answer_spots({
        spot_id(request, "zasedáni"): {"text": "zasedání", "confidence": 30}})(request))
    language_provider.on("lookup", found({}))
    workflow, project = run(tmp_path, language_provider,
                            PrintedOcr(readings={word_index("zasedání"): ("zasedáni", 60.0)}))

    assert [text for text, _ in exported(workflow, project)][word_index("zasedání")] == "zasedáni"
    overlay = workflow.overlay(project["id"], "lookup", 0)
    [word] = overlay["words"]
    assert (word["status"], word["text"], word["hint"]) == ("unresolved", "zasedáni", "zasedáni")
    assert overlay["counts"] == {"spots": 1, "searched": 1, "settled": 0, "markers": 0, "kept": 1}
    assert "0 illegible markers and 1 doubtful reading kept as read remain" in workflow.report_summary(project["id"])
