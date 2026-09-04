"""CSV merger for combining flashcard files."""

from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from flashcards_generator.application.dto.workflow import MergeDetails
from flashcards_generator.domain.exceptions import CSVMergeError

if TYPE_CHECKING:
    from flashcards_generator.application.dto.merge_request import (
        MergeCsvRequest,
    )


class CsvMerger:
    """Merge multiple CSV flashcard files into one."""

    @staticmethod
    def merge(request: MergeCsvRequest) -> int:
        """Merge all CSV files in folder_path into single output file.

        Args:
            request: MergeCsvRequest with folder, output name, dedup flag

        Returns:
            Number of rows written to merged file

        Raises:
            CSVMergeError: If folder doesn't exist or merge fails
        """
        return CsvMerger.merge_detailed(request).rows_written

    @staticmethod
    def merge_detailed(request: MergeCsvRequest) -> MergeDetails:
        """Merge CSV files and return before/after deduplication counts."""
        output_path = request.folder_path / request.output_filename
        try:
            csv_files = CsvMerger._source_files(request, output_path)
            return CsvMerger._write_rows(
                output_path, csv_files, deduplicate=request.deduplicate
            )

        except CSVMergeError:
            raise
        # This application boundary must translate every merge failure consistently.
        except Exception as e:
            raise CSVMergeError(request.folder_path, str(e)) from e

    @staticmethod
    def _source_files(
        request: MergeCsvRequest, output_path: Path
    ) -> list[Path]:
        if not request.folder_path.exists():
            raise CSVMergeError(request.folder_path, "Folder does not exist")
        pattern = "**/*.csv" if request.recursive else "*.csv"
        csv_files = [
            path
            for path in sorted(request.folder_path.glob(pattern))
            if path != output_path
        ]
        if not csv_files:
            raise CSVMergeError(
                request.folder_path, "No CSV files found in folder"
            )
        return csv_files

    @staticmethod
    def _write_rows(
        output_path: Path,
        csv_files: list[Path],
        *,
        deduplicate: bool,
    ) -> MergeDetails:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                newline="",
                encoding="utf-8",
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as out_f:
                temporary_path = Path(out_f.name)
                writer = csv.writer(out_f, quoting=csv.QUOTE_ALL)
                rows_before = 0
                rows_written = 0
                seen: set[tuple[str, str]] = set()
                for front, back in CsvMerger._iter_rows(csv_files):
                    rows_before += 1
                    if deduplicate:
                        key = (front.strip(), back.strip())
                        if key in seen:
                            continue
                        seen.add(key)
                    writer.writerow([front, back])
                    rows_written += 1
                out_f.flush()
                os.fsync(out_f.fileno())

            temporary_path.replace(output_path)
            return MergeDetails(
                rows_before=rows_before,
                rows_written=rows_written,
                duplicates_removed=rows_before - rows_written,
            )
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _iter_rows(csv_files: list[Path]) -> Iterator[tuple[str, str]]:
        for csv_file in csv_files:
            with open(csv_file, newline="", encoding="utf-8") as in_f:
                for row_number, row in enumerate(csv.reader(in_f), start=1):
                    if len(row) < 2:
                        continue
                    if len(row) != 2:
                        raise CSVMergeError(
                            csv_file,
                            f"Row {row_number} must contain exactly 2 columns; "
                            f"found {len(row)} columns",
                        )
                    yield row[0], row[1]
