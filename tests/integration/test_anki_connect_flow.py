"""CLI-level AnkiConnect integration coverage with local HTTP boundaries."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from pypdf import PdfWriter


class _AnkiServerState:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.error: str | None = None


class _AnkiHandler(BaseHTTPRequestHandler):
    state: _AnkiServerState

    def do_POST(self) -> None:
        content_length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(content_length))
        self.state.requests.append(request)

        action = request.get("action")
        if self.state.error is not None:
            response = {"result": None, "error": self.state.error}
        elif action == "createDeck":
            response = {"result": 1, "error": None}
        elif action == "addNotes":
            notes = request["params"]["notes"]
            response = {
                "result": list(range(100, 100 + len(notes))),
                "error": None,
            }
        else:
            response = {"result": None, "error": "unsupported action"}

        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def anki_server() -> Iterator[tuple[str, _AnkiServerState]]:
    state = _AnkiServerState()

    class Handler(_AnkiHandler):
        pass

    Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _create_notebooklm_fake(tmp_path: Path) -> Path:
    executable = tmp_path / "notebooklm"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
if args and args[0] == "create":
    print(json.dumps({"id": "nb1"}))
elif args[:2] == ["source", "add"]:
    print(json.dumps({"source_id": "src1"}))
elif args[:2] == ["generate", "flashcards"]:
    print(json.dumps({"task_id": "art1"}))
elif args[:2] == ["download", "flashcards"]:
    Path(args[-1]).write_text(
        json.dumps(
            [
                {
                    "front": "O núcleo contém {{c1::DNA}}.",
                    "back": "Material genético.",
                }
            ]
        )
    )
elif args and args[0] in {"artifact", "language", "source"}:
    pass
else:
    print(f"unsupported command: {args}", file=sys.stderr)
    raise SystemExit(1)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _run_real_cli(
    tmp_path: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[str]:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    topic_dir = input_dir / "unidade-1"
    topic_dir.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with (topic_dir / "aula.pdf").open("wb") as pdf_file:
        writer.write(pdf_file)

    notebooklm_dir = tmp_path / "bin"
    notebooklm_dir.mkdir()
    _create_notebooklm_fake(notebooklm_dir)

    environment = os.environ.copy()
    environment["PATH"] = f"{notebooklm_dir}{os.pathsep}{environment['PATH']}"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "flashcards_generator",
            "generate",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(tmp_path / "output"),
            "--timeout",
            "10",
            "--skip-auth-check",
            *arguments,
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.integration
def test_imports_cloze_cards_into_requested_deck(
    tmp_path: Path,
    anki_server: tuple[str, _AnkiServerState],
) -> None:
    anki_url, state = anki_server
    completed = _run_real_cli(
        tmp_path,
        [
            "--anki-deck",
            "Estácio::Disciplina::Unidade 1",
            "--anki-connect-url",
            anki_url,
            "--skip-auth-check",
        ],
    )

    assert completed.returncode == 0
    assert [request["action"] for request in state.requests] == [
        "createDeck",
        "addNotes",
    ]
    add_notes = state.requests[1]["params"]["notes"]
    assert add_notes == [
        {
            "deckName": "Estácio::Disciplina::Unidade 1",
            "modelName": "Cloze",
            "fields": {
                "Text": "O núcleo contém {{c1::DNA}}.",
                "Extra": "Material genético.",
            },
            "tags": ["unidade-1_aula"],
            "options": {
                "allowDuplicate": False,
                "duplicateScope": "deck",
                "duplicateScopeDeckName": "Estácio::Disciplina::Unidade 1",
            },
        }
    ]


@pytest.mark.integration
def test_reports_anki_connect_failure_without_claiming_success(
    tmp_path: Path,
    anki_server: tuple[str, _AnkiServerState],
) -> None:
    anki_url, state = anki_server
    state.error = "cannot create requested deck"

    completed = _run_real_cli(
        tmp_path,
        [
            "--anki-deck",
            "Estácio::Disciplina::Unidade 1",
            "--anki-connect-url",
            anki_url,
            "--skip-auth-check",
        ],
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert len(state.requests) == 1
    assert state.requests[0]["action"] == "createDeck"
    assert "AnkiConnect" in output
    assert "sucesso" not in output.lower()


@pytest.mark.integration
def test_existing_csv_export_remains_default(
    tmp_path: Path,
    anki_server: tuple[str, _AnkiServerState],
) -> None:
    _anki_url, state = anki_server
    output_dir = tmp_path / "output"
    completed = _run_real_cli(
        tmp_path,
        ["--output-dir", str(output_dir)],
    )

    csv_paths = list(output_dir.rglob("*.csv"))
    assert completed.returncode == 0
    assert len(csv_paths) == 1
    with csv_paths[0].open(newline="", encoding="utf-8") as csv_file:
        assert list(csv.reader(csv_file)) == [
            ["O núcleo contém {{c1::DNA}}.", "Material genético."]
        ]
    assert state.requests == []
