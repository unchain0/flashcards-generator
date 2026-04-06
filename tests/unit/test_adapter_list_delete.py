"""Tests for NotebookLM adapter list and delete methods."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from flashcards_generator.adapters.notebooklm_adapter import NotebookLMAdapter


class TestNotebookLMAdapterList:
    """Test list_notebooks method."""

    def test_list_notebooks_success(self):
        """Test successful notebook listing."""
        adapter = NotebookLMAdapter("notebooklm")

        notebooks_data = {
            "notebooks": [
                {"id": "nb1", "title": "Notebook 1"},
                {"id": "nb2", "title": "Notebook 2"},
            ]
        }

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (0, json.dumps(notebooks_data), "")
            result = adapter.list_notebooks()

        assert len(result) == 2
        assert result[0]["id"] == "nb1"

    def test_list_notebooks_with_days_filter(self):
        """Test listing notebooks with days filter."""
        adapter = NotebookLMAdapter("notebooklm")

        now = datetime.now(UTC)
        recent = now.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = (
            (now - timedelta(days=10))
            .replace(microsecond=0)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )

        notebooks_data = {
            "notebooks": [
                {"id": "nb1", "title": "Recent", "created_at": recent},
                {"id": "nb2", "title": "Old", "created_at": old},
            ]
        }

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (0, json.dumps(notebooks_data), "")
            result = adapter.list_notebooks(days=5)

        assert len(result) == 1
        assert result[0]["id"] == "nb1"

    def test_list_notebooks_empty_list(self):
        """Test listing when no notebooks exist."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (0, json.dumps({"notebooks": []}), "")
            result = adapter.list_notebooks()

        assert result == []

    def test_list_notebooks_array_response(self):
        """Test listing when response is an array instead of object."""
        adapter = NotebookLMAdapter("notebooklm")

        notebooks_data = [
            {"id": "nb1", "title": "Notebook 1"},
            {"id": "nb2", "title": "Notebook 2"},
        ]

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (0, json.dumps(notebooks_data), "")
            result = adapter.list_notebooks()

        assert len(result) == 2

    def test_list_notebooks_command_fails(self):
        """Test listing when command fails."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (1, "", "Error")
            result = adapter.list_notebooks()

        assert result == []

    def test_list_notebooks_invalid_json(self):
        """Test listing with invalid JSON response."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (0, "not json", "")
            result = adapter.list_notebooks()

        assert result == []

    def test_list_notebooks_non_dict_items(self):
        """Test filtering when notebook items are not dicts."""
        adapter = NotebookLMAdapter("notebooklm")

        notebooks_data = {
            "notebooks": [
                {"id": "nb1", "title": "Valid"},
                "not a dict",
                {"id": "nb2", "title": "Also valid"},
            ]
        }

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (0, json.dumps(notebooks_data), "")
            result = adapter.list_notebooks(days=30)

        assert len(result) == 2

    def test_list_notebooks_no_date_field(self):
        """Test filtering when notebook has no date field."""
        adapter = NotebookLMAdapter("notebooklm")

        notebooks_data = {
            "notebooks": [
                {"id": "nb1", "title": "No date"},
            ]
        }

        with patch.object(adapter, "_run_command") as mock_run:
            mock_run.return_value = (0, json.dumps(notebooks_data), "")
            result = adapter.list_notebooks(days=5)

        assert len(result) == 1


class TestNotebookLMAdapterParseDatetime:
    """Test _parse_datetime method."""

    def test_parse_datetime_iso_format(self):
        """Test parsing ISO format datetime."""
        adapter = NotebookLMAdapter("notebooklm")

        dt_str = "2024-01-15T10:30:00Z"
        result = adapter._parse_datetime(dt_str)

        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_parse_datetime_with_microseconds(self):
        """Test parsing datetime with microseconds."""
        adapter = NotebookLMAdapter("notebooklm")

        dt_str = "2024-01-15T10:30:00.123456Z"
        result = adapter._parse_datetime(dt_str)

        assert result is not None
        assert result.year == 2024

    def test_parse_datetime_space_format(self):
        """Test parsing space-separated datetime."""
        adapter = NotebookLMAdapter("notebooklm")

        dt_str = "2024-01-15 10:30:00"
        result = adapter._parse_datetime(dt_str)

        assert result is not None
        assert result.year == 2024

    def test_parse_datetime_date_only(self):
        """Test parsing date-only format."""
        adapter = NotebookLMAdapter("notebooklm")

        dt_str = "2024-01-15"
        result = adapter._parse_datetime(dt_str)

        assert result is not None
        assert result.year == 2024

    def test_parse_datetime_iso_no_timezone(self):
        """Test parsing ISO format without timezone (NotebookLM CLI format)."""
        adapter = NotebookLMAdapter("notebooklm")

        dt_str = "2024-01-15T10:30:00"
        result = adapter._parse_datetime(dt_str)

        assert result is not None
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 10
        assert result.tzinfo is not None  # Should have UTC timezone applied

    def test_parse_datetime_invalid(self):
        """Test parsing invalid datetime."""
        adapter = NotebookLMAdapter("notebooklm")

        dt_str = "not a date"
        result = adapter._parse_datetime(dt_str)

        assert result is None


class TestNotebookLMAdapterDeleteAll:
    """Test delete_all_notebooks method."""

    def test_delete_all_notebooks_success(self):
        """Test successful deletion of all notebooks."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = [
                {"id": "nb1"},
                {"id": "nb2"},
            ]

            with patch.object(adapter, "delete_notebook") as mock_delete:
                mock_delete.return_value = True
                deleted, failed = adapter.delete_all_notebooks()

        assert deleted == 2
        assert failed == 0

    def test_delete_all_notebooks_with_days(self):
        """Test deletion with days filter."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = [{"id": "nb1"}]

            with patch.object(adapter, "delete_notebook") as mock_delete:
                mock_delete.return_value = True
                adapter.delete_all_notebooks(days=7)

        mock_list.assert_called_once_with(days=7)

    def test_delete_all_notebooks_partial_failure(self):
        """Test when some deletions fail."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = [
                {"id": "nb1"},
                {"id": "nb2"},
                {"id": "nb3"},
            ]

            with patch.object(adapter, "delete_notebook") as mock_delete:
                # First succeeds, second fails, third succeeds
                mock_delete.side_effect = [True, False, True]
                deleted, failed = adapter.delete_all_notebooks()

        assert deleted == 2
        assert failed == 1

    def test_delete_all_notebooks_empty_list(self):
        """Test when no notebooks to delete."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = []
            deleted, failed = adapter.delete_all_notebooks()

        assert deleted == 0
        assert failed == 0

    def test_delete_all_notebooks_with_string_ids(self):
        """Test deletion when notebooks are strings not dicts."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = ["nb1", "nb2"]

            with patch.object(adapter, "delete_notebook") as mock_delete:
                mock_delete.return_value = True
                deleted, _failed = adapter.delete_all_notebooks()

        assert deleted == 2

    def test_delete_all_notebooks_no_id(self):
        """Test skipping notebooks without id."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = [
                {"id": "nb1"},
                {"no_id": "missing"},
                {"id": "nb2"},
            ]

            with patch.object(adapter, "delete_notebook") as mock_delete:
                mock_delete.return_value = True
                deleted, _failed = adapter.delete_all_notebooks()

        assert deleted == 2
        assert mock_delete.call_count == 2

    def test_delete_all_notebooks_with_progress(self):
        """Test deletion with progress bar display."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = [{"id": "nb1"}, {"id": "nb2"}]

            with patch.object(adapter, "delete_notebook") as mock_delete:
                mock_delete.return_value = True
                deleted, failed = adapter.delete_all_notebooks(show_progress=True)

        assert deleted == 2
        assert failed == 0
        assert mock_delete.call_count == 2
        # Verify silent mode is passed
        mock_delete.assert_called_with("nb2", silent=True)

    def test_delete_all_notebooks_empty_with_progress(self):
        """Test progress bar when no notebooks to delete."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = []
            deleted, failed = adapter.delete_all_notebooks(show_progress=True)

        assert deleted == 0
        assert failed == 0

    def test_delete_all_notebooks_no_id_with_progress(self):
        """Test progress bar skips notebooks without id."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = [
                {"id": "nb1"},
                {"no_id": "missing"},
                {"id": "nb2"},
            ]

            with patch.object(adapter, "delete_notebook") as mock_delete:
                mock_delete.return_value = True
                deleted, failed = adapter.delete_all_notebooks(show_progress=True)

        assert deleted == 2
        assert failed == 0
        assert mock_delete.call_count == 2

    def test_delete_all_notebooks_with_progress_and_failures(self):
        """Test progress bar counts failed deletions."""
        adapter = NotebookLMAdapter("notebooklm")

        with patch.object(adapter, "list_notebooks") as mock_list:
            mock_list.return_value = [{"id": "nb1"}, {"id": "nb2"}]

            with patch.object(adapter, "delete_notebook") as mock_delete:
                # First succeeds, second fails
                mock_delete.side_effect = [True, False]
                deleted, failed = adapter.delete_all_notebooks(show_progress=True)

        assert deleted == 1
        assert failed == 1
        assert mock_delete.call_count == 2
