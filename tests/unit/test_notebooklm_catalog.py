from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest

from flashcards_generator.integrations.notebooklm.catalog import (
    created_on_or_after,
    delete_notebooks,
    normalize_notebooks,
    notebook_id,
    parse_notebook_datetime,
)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"notebooks": [{"id": "a"}, "invalid"]}, [{"id": "a"}]),
        ([{"id": "a"}], [{"id": "a"}]),
        ({"notebooks": "invalid"}, []),
        ({"notebooks": None}, []),
        (None, []),
    ],
)
def test_normalize_notebooks(data, expected) -> None:
    assert normalize_notebooks(data) == expected


@pytest.mark.parametrize(
    "value",
    [
        "2024-01-15T10:30:00.123456Z",
        "2024-01-15T10:30:00Z",
        "2024-01-15T10:30:00",
        "2024-01-15 10:30:00",
        "2024-01-15",
    ],
)
def test_parse_notebook_datetime_accepts_supported_formats(value: str) -> None:
    parsed = parse_notebook_datetime(value)

    assert parsed is not None
    assert parsed.tzinfo == UTC


@pytest.mark.parametrize("value", [None, 10, "invalid"])
def test_parse_notebook_datetime_rejects_invalid_values(value) -> None:
    assert parse_notebook_datetime(value) is None


def test_created_on_or_after_includes_missing_invalid_and_boundary_dates() -> (
    None
):
    cutoff = datetime(2024, 1, 15, tzinfo=UTC)

    assert created_on_or_after({"id": "missing"}, cutoff)
    assert created_on_or_after({"created_at": "invalid"}, cutoff)
    assert created_on_or_after({"created_at": "2024-01-15"}, cutoff)
    assert not created_on_or_after({"created": "2024-01-14"}, cutoff)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"id": "nb-1"}, "nb-1"),
        ("nb-2", "nb-2"),
        ({"id": ""}, None),
        ({"id": 123}, None),
        ({"title": "no id"}, None),
        (None, None),
    ],
)
def test_notebook_id_accepts_only_nonempty_strings(value, expected) -> None:
    assert notebook_id(value) == expected


def test_delete_notebooks_without_progress_preserves_order_and_logs() -> None:
    delete = Mock(side_effect=[True, False])
    log = Mock()
    records = [{"id": "one"}, {}, {"id": "two"}]

    result = delete_notebooks(records, delete, False, log)

    assert result == (1, 1)
    assert delete.call_args_list == [
        (("one", False),),
        (("two", False),),
    ]
    assert log.info.call_count == 3


def test_delete_notebooks_with_progress_advances_for_missing_ids() -> None:
    delete = Mock(return_value=True)
    log = Mock()
    progress = Mock()
    progress.__enter__ = Mock(return_value=progress)
    progress.__exit__ = Mock(return_value=None)

    with patch(
        "flashcards_generator.integrations.notebooklm.catalog.Progress",
        return_value=progress,
    ):
        result = delete_notebooks(
            [{"id": "one"}, {}, "two"], delete, True, log
        )

    assert result == (2, 0)
    assert delete.call_args_list == [
        (("one", True),),
        (("two", True),),
    ]
    assert progress.update.call_count == 3


@pytest.mark.parametrize("show_progress", [False, True])
def test_delete_notebooks_empty_list_returns_zero(show_progress: bool) -> None:
    delete = Mock()

    assert delete_notebooks([], delete, show_progress, Mock()) == (0, 0)
    delete.assert_not_called()
