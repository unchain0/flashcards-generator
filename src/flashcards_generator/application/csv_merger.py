"""CSV merger for combining flashcard files."""

import csv
from typing import TYPE_CHECKING

from flashcards_generator.domain.exceptions import CSVMergeError

if TYPE_CHECKING:
    from flashcards_generator.application.dto.merge_request import MergeCsvRequest


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
        if not request.folder_path.exists():
            raise CSVMergeError(request.folder_path, "Folder does not exist")

        pattern = "**/*.csv" if request.recursive else "*.csv"
        csv_files = sorted(request.folder_path.glob(pattern))

        # Exclude output file if it exists
        output_path = request.folder_path / request.output_filename
        csv_files = [f for f in csv_files if f != output_path]

        if not csv_files:
            raise CSVMergeError(request.folder_path, "No CSV files found in folder")

        seen = set() if request.deduplicate else None
        total_rows = 0

        try:
            with open(output_path, "w", newline="", encoding="utf-8") as out_f:
                writer = csv.writer(out_f, quoting=csv.QUOTE_ALL)

                for csv_file in csv_files:
                    with open(csv_file, newline="", encoding="utf-8") as in_f:
                        reader = csv.reader(in_f)
                        for row in reader:
                            # Validate 2-column format
                            if len(row) < 2:
                                continue

                            front, back = row[0], row[1]

                            # Deduplication check
                            if seen is not None:
                                key = (front.strip(), back.strip())
                                if key in seen:
                                    continue
                                seen.add(key)

                            writer.writerow([front, back])
                            total_rows += 1

            return total_rows

        except Exception as e:
            raise CSVMergeError(request.folder_path, str(e)) from e
