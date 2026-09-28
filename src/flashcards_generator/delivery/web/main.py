from __future__ import annotations

import uvicorn

from flashcards_generator.delivery.web.app import app
from flashcards_generator.delivery.web.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
