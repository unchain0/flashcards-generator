from __future__ import annotations

import uvicorn

from flashcards_generator.delivery.companion.app import create_app
from flashcards_generator.delivery.companion.config import (
    COMPANION_PORT,
    CompanionSettings,
)


def main() -> None:
    settings = CompanionSettings()
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=COMPANION_PORT,
    )
