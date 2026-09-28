from __future__ import annotations

import os
from pathlib import Path

from flashcards_generator.delivery.web.main import main

fake_bin = Path(__file__).with_name("fake-bin")
current_path = os.environ.get("PATH", "")
os.environ["PATH"] = (
    f"{fake_bin}{os.pathsep}{current_path}" if current_path else str(fake_bin)
)

main()
