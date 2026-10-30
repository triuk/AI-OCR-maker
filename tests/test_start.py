"""The start script serves the Czech WebUI on loopback, bound to a per-process session token."""
from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def started(tmp_path):
    process = subprocess.Popen([str(ROOT / "start.sh"), "--server", "--port", "0", "--data-dir", str(tmp_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        line = process.stdout.readline()
        match = re.search(r"http://127\.0\.0\.1:\d+/", line)
        assert match, line
        yield match.group(0)
    finally:
        process.terminate()
        process.wait(10)


def get(url, token=None):
    request = urllib.request.Request(url, headers={"X-Ocrmaker-Token": token} if token else {})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def test_the_start_script_serves_the_czech_webui_on_loopback_with_a_session_token(started):
    assert '<html lang="cs">' in get(started)
    token = json.loads(get(started + "bootstrap.json"))["token"]
    assert len(token) >= 32

    with pytest.raises(urllib.error.HTTPError) as refused:
        get(started + "api/projects")
    assert refused.value.code == 401
    assert json.loads(get(started + "api/projects", token))["projects"] == []
