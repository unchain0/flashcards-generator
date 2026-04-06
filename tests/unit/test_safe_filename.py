"""Tests for safe filename helper function."""

from flashcards_generator.application.use_cases import _safe_filename


class TestSafeFilename:
    """Test _safe_filename helper function."""

    def test_short_filename_unchanged(self):
        """Test that short filenames are not modified."""
        result = _safe_filename("short_name", "_raw.json")
        assert result == "short_name_raw.json"

    def test_long_filename_truncated(self):
        """Test that long filenames are truncated with hash."""
        long_name = "a" * 300
        result = _safe_filename(long_name, "_raw.json")

        # Should be within limits
        assert len(result) <= 200 + len("_raw.json")
        # Should end with suffix
        assert result.endswith("_raw.json")
        # Should contain a hash (8 hex chars + underscore)
        assert "_" in result.replace("_raw.json", "")

    def test_long_filename_preserves_uniqueness(self):
        """Test that different long names produce different results."""
        long_name1 = "a" * 300
        long_name2 = "b" * 300

        result1 = _safe_filename(long_name1, "_raw.json")
        result2 = _safe_filename(long_name2, "_raw.json")

        assert result1 != result2

    def test_empty_base_name(self):
        """Test with empty base name."""
        result = _safe_filename("", ".txt")
        assert result == ".txt"

    def test_no_suffix(self):
        """Test without suffix."""
        result = _safe_filename("filename", "")
        assert result == "filename"

    def test_very_long_with_suffix(self):
        """Test edge case with very long suffix."""
        long_suffix = "_very_long_suffix_that_might_cause_issues.json"
        result = _safe_filename("name", long_suffix)

        # Should still be within limits
        assert len(result) <= 200 + len(long_suffix)
        assert result.endswith(long_suffix)
