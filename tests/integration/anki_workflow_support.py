from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import TypedDict

import httpx
import pytest
from pydantic import JsonValue, TypeAdapter
from pypdf import PdfWriter

from flashcards_generator.delivery.composition import create_workflows
from flashcards_generator.services.contracts import (
    CancellationToken,
    GenerationOutcome,
    NullProgressReporter,
)
from flashcards_generator.services.dto.generate_request import (
    GenerateFlashcardsRequest,
)
from flashcards_generator.services.workflows import ApplicationWorkflows


class _AnkiRequest(TypedDict):
    action: str
    version: int
    params: dict[str, JsonValue]


@dataclass(slots=True)
class _AnkiState:
    requests: list[_AnkiRequest] = field(default_factory=list)
    attempted_urls: list[str] = field(default_factory=list)
    reject_notes: bool = False


@dataclass(frozen=True, slots=True)
class _AnkiEndpoint:
    url: str
    state: _AnkiState


@dataclass(frozen=True, slots=True)
class _GenerationScenario:
    workflows: ApplicationWorkflows
    request: GenerateFlashcardsRequest


_ANKI_REQUEST_ADAPTER = TypeAdapter(_AnkiRequest)
_FAKE_NOTEBOOKLM = (
    "#!__PYTHON__\n"
    "import json\n"
    "import os\n"
    "import sys\n"
    "from pathlib import Path\n"
    "args = sys.argv[1:]\n"
    "if args[:2] == ['--profile', 'integration-test']:\n"
    "    args = args[2:]\n"
    "if not os.environ.get('NOTEBOOKLM_HOME'):\n"
    "    raise SystemExit(3)\n"
    "if args[0] == 'create':\n"
    "    print(json.dumps({'id': 'nb1'}))\n"
    "elif args[:2] == ['source', 'add']:\n"
    "    print(json.dumps({'source_id': 'src1'}))\n"
    "elif args[:2] in (['source', 'wait'], ['artifact', 'wait']):\n"
    "    raise SystemExit(0)\n"
    "elif args[:2] == ['generate', 'flashcards']:\n"
    "    print(json.dumps({'task_id': 'art1'}))\n"
    "elif args[:2] == ['download', 'flashcards']:\n"
    "    cards = [{'front': 'O núcleo contém {{c1::DNA}}.',\n"
    "              'back': 'Material genético.'}]\n"
    "    Path(args[-1]).write_text(json.dumps(cards), encoding='utf-8')\n"
    "elif args[0] == 'delete' or args[:2] == ['language', 'set']:\n"
    "    raise SystemExit(0)\n"
    "else:\n"
    "    raise SystemExit(2)\n"
)


def _provide_anki_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_AnkiEndpoint]:
    state = _AnkiState()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request_size = int(self.headers.get("Content-Length", "0"))
            request = _ANKI_REQUEST_ADAPTER.validate_json(
                self.rfile.read(request_size)
            )
            state.requests.append(request)
            error = (
                "controlled import failure"
                if state.reject_notes and request["action"] == "addNotes"
                else None
            )
            result: JsonValue = (
                1
                if request["action"] == "createDeck"
                else [42]
                if request["action"] == "addNotes"
                else None
            )
            body = json.dumps({"result": result, "error": error}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = _AnkiEndpoint(
        url=f"http://127.0.0.1:{server.server_port}", state=state
    )
    original_post = httpx.Client.post

    def guarded_post(
        client: httpx.Client,
        url: str,
        *,
        json: JsonValue | None = None,
    ) -> httpx.Response:
        state.attempted_urls.append(url)
        if url != endpoint.url:
            raise AssertionError("AnkiConnect attempted a non-test URL")
        return original_post(client, url, json=json)

    monkeypatch.setattr(httpx.Client, "post", guarded_post)
    try:
        yield endpoint
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> _GenerationScenario:
    input_dir = tmp_path / "input"
    topic_dir = input_dir / "Unidade 1"
    topic_dir.mkdir(parents=True)
    pdf_path = topic_dir / "Aula.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as file_obj:
        writer.write(file_obj)

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    fake_path = tmp_path / "bin" / "notebooklm"
    fake_path.parent.mkdir()
    fake_path.write_text(
        _FAKE_NOTEBOOKLM.replace("__PYTHON__", sys.executable),
        encoding="utf-8",
    )
    fake_path.chmod(0o700)
    notebooklm_home = tmp_path / "notebooklm-home"
    notebooklm_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    return _GenerationScenario(
        workflows=create_workflows(
            notebooklm_path=str(fake_path),
            notebooklm_profile="integration-test",
            notebooklm_home=notebooklm_home,
        ),
        request=GenerateFlashcardsRequest(
            input_dir=input_dir,
            output_dir=output_dir,
            timeout=5,
            resume=False,
        ),
    )


def _generate(scenario: _GenerationScenario) -> GenerationOutcome:
    return scenario.workflows.generate(
        scenario.request,
        NullProgressReporter(),
        CancellationToken(),
    )
