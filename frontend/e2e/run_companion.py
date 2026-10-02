from __future__ import annotations

import os
from pathlib import Path

from pypdf import PdfWriter

from flashcards_generator.delivery.companion.main import main

fake_bin = Path(__file__).with_name("fake-bin")
os.environ["PATH"] = f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"
fixture_path = Path(os.environ["FLASHCARDS_E2E_DIRECTORY"]) / "lesson.pdf"
with PdfWriter() as writer:
    writer.add_blank_page(width=72, height=72)
    writer.write(fixture_path)

main()
