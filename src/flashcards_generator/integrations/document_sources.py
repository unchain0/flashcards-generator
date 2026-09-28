from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Final

from flashcards_generator.integrations.logging_config import get_logger
from flashcards_generator.services.ports.document_sources import (
    DocumentSelection,
)

logger = get_logger("use_cases")

SOURCE_SUFFIXES: Final = (".pdf", ".pptx")
SUPPORTED_EXTENSIONS: Final[set[str]] = set(SOURCE_SUFFIXES)


def find_all_sources(
    input_path: Path, selection: DocumentSelection
) -> list[Path]:
    if selection.explicit_files:
        return _find_explicit_files(input_path, selection.explicit_files)

    return _apply_filters(_find_supported_sources(input_path), selection)


def _find_supported_sources(input_path: Path) -> list[Path]:
    return [
        file_path
        for suffix in SOURCE_SUFFIXES
        for file_path in input_path.rglob(f"*{suffix}")
        if is_safe_source(file_path, input_path)
    ]


def _apply_filters(
    files: list[Path], selection: DocumentSelection
) -> list[Path]:
    if selection.include_pattern:
        files = _filter_files(files, selection.include_pattern, exclude=False)
        logger.info(f"Include filter '{selection.include_pattern}' applied")
    if selection.exclude_pattern:
        files = _filter_files(files, selection.exclude_pattern, exclude=True)
        logger.info(f"Exclude filter '{selection.exclude_pattern}' applied")
    return files


def _find_explicit_files(
    input_path: Path, explicit_files: tuple[str, ...]
) -> list[Path]:
    files: list[Path] = []
    for file_name in explicit_files:
        file_path = input_path / file_name
        if is_safe_source(file_path, input_path):
            try:
                files.append(file_path.resolve(strict=True))
            except (OSError, ValueError) as error:
                logger.warning(f"Skipping explicit file {file_name}: {error}")
        else:
            logger.warning(f"Explicit file not found: {file_name}")
    return files


def _filter_files(
    files: list[Path], pattern: str, *, exclude: bool
) -> list[Path]:
    return [
        file_path
        for file_path in files
        if fnmatch.fnmatch(file_path.name, pattern) != exclude
    ]


def is_safe_source(file_path: Path, input_path: Path) -> bool:
    try:
        if file_path.is_symlink():
            logger.warning(f"Skipping symlink: {file_path}")
            return False

        resolved_file = file_path.resolve(strict=True)
        resolved_input = input_path.resolve(strict=True)
        return _is_valid_resolved_source(
            resolved_file, resolved_input, file_path
        )
    except (OSError, ValueError) as error:
        logger.warning(f"Skipping invalid file path {file_path}: {error}")
        return False


def _is_valid_resolved_source(
    resolved_file: Path, resolved_input: Path, original_path: Path
) -> bool:
    try:
        resolved_file.relative_to(resolved_input)
    except ValueError:
        logger.warning(
            f"Skipping file outside input directory: {original_path}"
        )
        return False
    if not resolved_file.is_file():
        logger.warning(f"Skipping non-file path: {original_path}")
        return False
    if resolved_file.suffix.lower() not in SUPPORTED_EXTENSIONS:
        logger.warning(f"Skipping unsupported file type: {original_path}")
        return False
    if resolved_file.stat().st_size == 0:
        logger.warning(f"Skipping empty file: {original_path}")
        return False
    return True


def get_deck_name(pdf_path: Path, input_path: Path) -> str:
    relative_path = pdf_path.relative_to(input_path)
    name_parts = [*relative_path.parent.parts, relative_path.stem]
    return "_".join(name_parts)


def get_output_subdir(
    pdf_path: Path, input_path: Path, output_path: Path
) -> Path:
    relative_path = pdf_path.relative_to(input_path)
    result_root = output_path.resolve(strict=True)
    parent = relative_path.parent
    subdir = result_root if parent == Path(".") else result_root / parent
    _ensure_within_output_root(
        subdir.resolve(strict=False), result_root, subdir
    )
    subdir.mkdir(parents=True, exist_ok=True)
    resolved_subdir = subdir.resolve(strict=True)
    _ensure_within_output_root(resolved_subdir, result_root, subdir)
    return resolved_subdir


def _ensure_within_output_root(
    resolved_path: Path, result_root: Path, requested_path: Path
) -> None:
    try:
        resolved_path.relative_to(result_root)
    except ValueError as error:
        raise OSError(
            f"Output path escaped result root: {requested_path}"
        ) from error


class FileSystemDocumentSources:
    def find_all_sources(
        self, input_path: Path, selection: DocumentSelection
    ) -> list[Path]:
        return find_all_sources(input_path, selection)

    def is_safe_source(self, file_path: Path, input_path: Path) -> bool:
        return is_safe_source(file_path, input_path)

    def get_deck_name(self, source_path: Path, input_path: Path) -> str:
        return get_deck_name(source_path, input_path)

    def get_output_subdir(
        self, source_path: Path, input_path: Path, output_path: Path
    ) -> Path:
        return get_output_subdir(source_path, input_path, output_path)
