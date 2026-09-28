from pathlib import Path

import pytest

from flashcards_generator.integrations import document_sources


def _write_source(path: Path, content: bytes = b"source") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_explicit_selection_keeps_order_and_ignores_filters(tmp_path):
    input_dir = tmp_path / "input"
    second = _write_source(input_dir / "second.pptx")
    first = _write_source(input_dir / "first.pdf")
    selection = document_sources.DocumentSelection(
        explicit_files=("second.pptx", "first.pdf", "second.pptx"),
        include_pattern="missing*",
        exclude_pattern="*",
    )

    assert document_sources.find_all_sources(input_dir, selection) == [
        second.resolve(),
        first.resolve(),
        second.resolve(),
    ]


def test_explicit_selection_skips_invalid_files(tmp_path):
    input_dir = tmp_path / "input"
    valid = _write_source(input_dir / "valid.pdf")
    selection = document_sources.DocumentSelection(
        explicit_files=("missing.pdf", "valid.pdf")
    )

    assert document_sources.find_all_sources(input_dir, selection) == [
        valid.resolve()
    ]


def test_automatic_discovery_orders_extensions_and_filters_by_basename(
    tmp_path,
):
    input_dir = tmp_path / "input"
    paths = [
        _write_source(input_dir / "z" / "lesson-drop.pptx"),
        _write_source(input_dir / "a" / "reference.pdf"),
        _write_source(input_dir / "b" / "lesson-keep.pdf"),
        _write_source(input_dir / "c" / "other.pptx"),
    ]

    discovered = document_sources.find_all_sources(
        input_dir, document_sources.DocumentSelection()
    )
    assert [path.suffix for path in discovered] == [
        ".pdf",
        ".pdf",
        ".pptx",
        ".pptx",
    ]
    assert set(discovered) == set(paths)

    filtered = document_sources.find_all_sources(
        input_dir,
        document_sources.DocumentSelection(
            include_pattern="lesson*", exclude_pattern="*drop*"
        ),
    )
    assert filtered == [input_dir / "b" / "lesson-keep.pdf"]


def test_exclude_filter_applies_without_include_filter(tmp_path):
    input_dir = tmp_path / "input"
    kept = _write_source(input_dir / "kept.pdf")
    _write_source(input_dir / "skip.pdf")

    sources = document_sources.find_all_sources(
        input_dir, document_sources.DocumentSelection(exclude_pattern="skip*")
    )

    assert sources == [kept]


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("valid.pdf", b"content", True),
        ("valid.PDF", b"content", True),
        ("empty.pdf", b"", False),
        ("unsupported.txt", b"content", False),
    ],
)
def test_safe_source_checks_extension_and_content(
    tmp_path, name, content, expected
):
    input_dir = tmp_path / "input"
    source = _write_source(input_dir / name, content)

    assert document_sources.is_safe_source(source, input_dir) is expected


def test_safe_source_rejects_symlink_directory_missing_and_external_paths(
    tmp_path,
):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external = _write_source(external_dir / "external.pdf")
    linked_dir = input_dir / "linked"
    linked_dir.symlink_to(external_dir, target_is_directory=True)
    linked_file = input_dir / "linked-file.pdf"
    linked_file.symlink_to(external)
    directory = input_dir / "directory.pdf"
    directory.mkdir()

    assert not document_sources.is_safe_source(linked_file, input_dir)
    assert not document_sources.is_safe_source(
        linked_dir / external.name, input_dir
    )
    assert not document_sources.is_safe_source(directory, input_dir)
    assert not document_sources.is_safe_source(
        input_dir / "missing.pdf", input_dir
    )
    assert not document_sources.is_safe_source(external, input_dir)


def test_safe_source_returns_false_when_path_resolution_raises_value_error(
    tmp_path, monkeypatch
):
    source = tmp_path / "input" / "source.pdf"
    source.parent.mkdir()
    source.touch()

    def fail_resolve(self, *, strict=False):
        raise ValueError("invalid path")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert not document_sources.is_safe_source(source, source.parent)


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("late stat"),
        PermissionError("late stat"),
        ValueError("late stat"),
    ],
    ids=["disappeared", "permission", "invalid-path"],
)
def test_safe_source_rejects_late_stat_errors(tmp_path, monkeypatch, error):
    input_dir = tmp_path / "input"
    source = _write_source(input_dir / "source.pdf")
    original_stat = Path.stat

    def fail_source_stat(path, *, follow_symlinks=True):
        if path == source:
            raise error
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", fail_source_stat)

    assert not document_sources.is_safe_source(source, input_dir)


def test_safe_source_rejects_file_removed_after_is_file_succeeds(
    tmp_path, monkeypatch
):
    input_dir = tmp_path / "input"
    source = _write_source(input_dir / "source.pdf")
    original_is_file = Path.is_file

    def remove_after_check(path, *, follow_symlinks=True):
        exists = original_is_file(path, follow_symlinks=follow_symlinks)
        if path == source and exists:
            path.unlink()
        return exists

    monkeypatch.setattr(Path, "is_file", remove_after_check)

    assert not document_sources.is_safe_source(source, input_dir)
    assert not source.exists()


def test_automatic_discovery_skips_late_stat_error_and_keeps_valid_source(
    tmp_path, monkeypatch
):
    input_dir = tmp_path / "input"
    rejected = _write_source(input_dir / "a-rejected.pdf")
    valid = _write_source(input_dir / "b-valid.pdf")
    original_rglob = Path.rglob
    original_stat = Path.stat

    def ordered_rglob(path, pattern):
        if path == input_dir:
            return iter([rejected, valid] if pattern == "*.pdf" else [])
        return original_rglob(path, pattern)

    def fail_rejected_stat(path, *, follow_symlinks=True):
        if path == rejected:
            raise FileNotFoundError("source disappeared")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "rglob", ordered_rglob)
    monkeypatch.setattr(Path, "stat", fail_rejected_stat)

    sources = document_sources.find_all_sources(
        input_dir, document_sources.DocumentSelection()
    )

    assert sources == [valid]


def test_explicit_discovery_skips_final_resolve_error_and_keeps_duplicates(
    tmp_path, monkeypatch
):
    input_dir = tmp_path / "input"
    rejected = _write_source(input_dir / "a-rejected.pdf")
    valid = _write_source(input_dir / "b-valid.pdf")
    original_resolve = Path.resolve
    rejected_resolutions = 0

    def fail_final_resolve(path, *, strict=False):
        nonlocal rejected_resolutions
        if path == rejected:
            rejected_resolutions += 1
            if rejected_resolutions == 2:
                raise PermissionError("source disappeared")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_final_resolve)

    sources = document_sources.find_all_sources(
        input_dir,
        document_sources.DocumentSelection(
            explicit_files=("a-rejected.pdf", "b-valid.pdf", "b-valid.pdf")
        ),
    )

    assert sources == [valid.resolve(), valid.resolve()]
    assert rejected_resolutions == 2


def test_safe_source_does_not_swallow_unexpected_stat_error(
    tmp_path, monkeypatch
):
    input_dir = tmp_path / "input"
    source = _write_source(input_dir / "source.pdf")
    unexpected_error = RuntimeError("unexpected stat failure")
    original_stat = Path.stat

    def fail_source_stat(path, *, follow_symlinks=True):
        if path == source:
            raise unexpected_error
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", fail_source_stat)

    with pytest.raises(RuntimeError) as result:
        document_sources.is_safe_source(source, input_dir)

    assert result.value is unexpected_error


def test_deck_name_uses_relative_parent_and_stem(tmp_path):
    source = tmp_path / "input" / "tema" / "sub" / "documento.pdf"

    assert document_sources.get_deck_name(source, tmp_path / "input") == (
        "tema_sub_documento"
    )


def test_output_subdir_returns_root_for_flat_sources_and_reuses_directories(
    tmp_path,
):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()
    flat_source = input_dir / "documento.pdf"
    nested_source = _write_source(input_dir / "tema" / "sub" / "documento.pdf")

    assert (
        document_sources.get_output_subdir(flat_source, input_dir, output_dir)
        == output_dir
    )
    expected = output_dir / "tema" / "sub"
    assert (
        document_sources.get_output_subdir(
            nested_source, input_dir, output_dir
        )
        == expected
    )
    assert (
        document_sources.get_output_subdir(
            nested_source, input_dir, output_dir
        )
        == expected
    )


def test_output_subdir_rejects_external_symlink_before_creating_child(
    tmp_path,
):
    input_dir = tmp_path / "input"
    source = _write_source(input_dir / "tema" / "sub" / "documento.pdf")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    (output_dir / "tema").symlink_to(external_dir, target_is_directory=True)

    with pytest.raises(OSError, match="escaped result root"):
        document_sources.get_output_subdir(source, input_dir, output_dir)

    assert not (external_dir / "sub").exists()


def test_output_subdir_propagates_creation_failure(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    source = _write_source(input_dir / "tema" / "source.pdf")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    requested = output_dir / "tema"
    creation_error = OSError("output creation failed")
    original_mkdir = Path.mkdir

    def fail_requested_mkdir(path, *args, **kwargs):
        if path == requested:
            raise creation_error
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_requested_mkdir)

    with pytest.raises(OSError) as result:
        document_sources.get_output_subdir(source, input_dir, output_dir)

    assert result.value is creation_error


def test_output_subdir_rejects_escape_after_directory_creation(
    tmp_path, monkeypatch
):
    input_dir = tmp_path / "input"
    source = _write_source(input_dir / "tema" / "source.pdf")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    requested = output_dir / "tema"
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    original_mkdir = Path.mkdir

    def replace_requested_with_symlink(path, *args, **kwargs):
        if path == requested:
            path.symlink_to(external_dir, target_is_directory=True)
            return None
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", replace_requested_with_symlink)

    with pytest.raises(OSError, match="escaped result root"):
        document_sources.get_output_subdir(source, input_dir, output_dir)
