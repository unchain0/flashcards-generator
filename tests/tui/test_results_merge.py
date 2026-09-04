"""Failing-first Pilot coverage for Results and Merge workflows."""

from __future__ import annotations

import csv
from pathlib import Path
from threading import Event

import anyio
import pytest

from flashcards_generator.application.dto.merge_request import MergeCsvRequest
from flashcards_generator.application.dto.workflow import MergeOutcome
from flashcards_generator.application.workflows import ApplicationWorkflows
from flashcards_generator.domain.entities import Deck, Flashcard
from flashcards_generator.interfaces.tui.app import FlashcardsApp


class RealMergeService:
    """Spy around the shared workflow and its real application merger."""

    def __init__(self) -> None:
        self.completed = Event()
        self.request: MergeCsvRequest | None = None
        self.outcome: MergeOutcome | None = None
        self.workflows = ApplicationWorkflows(object(), object())

    def merge(self, request: MergeCsvRequest) -> MergeOutcome:
        self.request = request
        self.outcome = self.workflows.merge(request)
        self.completed.set()
        return self.outcome


class MergeServices:
    """Minimal injected service bundle for the merge Pilot."""

    def __init__(self, merger: RealMergeService) -> None:
        self.merger = merger

    def merge(self, request: MergeCsvRequest) -> MergeOutcome:
        return self.merger.merge(request)

    def cancel_management(self) -> None:
        return


class ResultsServices:
    """Minimal service boundary for Results-only navigation checks."""

    def cancel_management(self) -> None:
        return


@pytest.mark.anyio
async def test_merge_uses_real_request_and_renders_valid_csv(
    tmp_path: Path,
) -> None:
    """Given CSV files, the TUI delegates and displays real merge counts."""
    folder = tmp_path / "csvs"
    folder.mkdir()
    source = folder / "one.csv"
    source.write_text("Q1,A1\nQ2,A2\n", encoding="utf-8")
    (folder / "two.csv").write_text(
        "Q1,A1\nQ3,A3\n",
        encoding="utf-8",
    )
    merger = RealMergeService()
    app = FlashcardsApp(services=MergeServices(merger))

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("m")
        app.query_one("#merge-folder").value = str(folder)
        app.query_one("#merge-output").value = "combined.csv"
        app.query_one("#merge-deduplicate").value = True
        await pilot.click("#merge-start")

        completed = await anyio.to_thread.run_sync(merger.completed.wait, 5)
        assert merger.request is not None
        assert merger.outcome is not None
        output = folder / "combined.csv"
        with output.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
        assert (
            completed,
            isinstance(merger.request, MergeCsvRequest),
            merger.request.deduplicate,
            merger.request.recursive,
            rows,
            merger.outcome.rows_before,
            merger.outcome.rows_written,
            merger.outcome.duplicates_removed,
            str(app.query_one("#merge-count").render()),
            str(app.query_one("#results-csv").render()),
        ) == (
            True,
            True,
            True,
            True,
            [["Q1", "A1"], ["Q2", "A2"], ["Q3", "A3"]],
            4,
            3,
            1,
            "4 row(s) before, 3 written, 1 duplicate(s) removed",
            "combined.csv - 3 row(s) (4 before, 1 duplicate(s) removed)",
        )


@pytest.mark.anyio
async def test_results_preview_and_actions_are_wired_to_navigation(
    tmp_path: Path,
) -> None:
    """Given a completed deck, Results renders preview and next actions."""
    app = FlashcardsApp(services=ResultsServices())
    output = tmp_path / "cards.csv"
    output.write_text("front,back\nQ,A\n", encoding="utf-8")

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        results = app.query_one("#results-panel")
        results.show_decks([
            Deck(name="Deck", flashcards=[Flashcard(front="Q", back="A")])
        ])
        results.show_csv(output, 1)
        assert "Q" in str(app.query_one("#results-preview").render())
        assert "Deck" in str(app.query_one("#results-cards").render())
        assert "cards.csv" in str(app.query_one("#results-csv").render())

        await pilot.pause()
        await pilot.click("#results-new")
        await pilot.pause()
        assert app.query_one("#main-tabs").active == "generate"
        await pilot.press("r")
        await pilot.pause()
        await pilot.click("#results-merge")
        assert app.query_one("#main-tabs").active == "merge"
