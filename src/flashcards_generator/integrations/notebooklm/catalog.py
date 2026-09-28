from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)

from flashcards_generator.integrations.notebooklm.response_parser import (
    JSONValue,
)

if TYPE_CHECKING:
    from loguru import Logger


DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)

type DeleteNotebook = Callable[[str, bool], bool]


def normalize_notebooks(data: JSONValue | None) -> list[dict[str, JSONValue]]:
    notebooks = data.get("notebooks", []) if isinstance(data, dict) else data
    if not isinstance(notebooks, list):
        return []
    return [item for item in notebooks if isinstance(item, dict)]


def parse_notebook_datetime(value: JSONValue) -> datetime | None:
    if not isinstance(value, str):
        return None
    for date_format in DATETIME_FORMATS:
        try:
            return datetime.strptime(value, date_format).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def created_on_or_after(
    notebook: dict[str, JSONValue], cutoff: datetime
) -> bool:
    created = parse_notebook_datetime(
        notebook.get("created_at") or notebook.get("created")
    )
    return created is None or created >= cutoff


def notebook_id(value: object) -> str | None:
    identifier = value.get("id") if isinstance(value, dict) else value
    return identifier if isinstance(identifier, str) and identifier else None


def delete_notebooks(
    notebooks: Sequence[object],
    delete: DeleteNotebook,
    show_progress: bool,
    log: Logger,
) -> tuple[int, int]:
    if show_progress:
        outcomes = _delete_with_progress(notebooks, delete)
    else:
        outcomes = _delete_without_progress(notebooks, delete, log)
    return outcomes.count(True), outcomes.count(False)


def _delete_with_progress(
    notebooks: Sequence[object], delete: DeleteNotebook
) -> list[bool]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        console=Console(),
    ) as progress:
        task = progress.add_task(
            f"Deleting {len(notebooks)} notebooks...",
            total=len(notebooks),
        )
        outcomes = []
        for notebook in notebooks:
            identifier = notebook_id(notebook)
            if identifier:
                outcomes.append(delete(identifier, True))
            progress.update(task, advance=1)
    return outcomes


def _delete_without_progress(
    notebooks: Sequence[object], delete: DeleteNotebook, log: Logger
) -> list[bool]:
    log.info(f"Found {len(notebooks)} notebook(s) to delete...")
    outcomes = []
    for index, notebook in enumerate(notebooks, 1):
        identifier = notebook_id(notebook)
        if identifier:
            log.info(f"[{index}/{len(notebooks)}] Deleting notebook...")
            outcomes.append(delete(identifier, False))
    return outcomes
