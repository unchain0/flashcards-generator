from __future__ import annotations

from litestar import get
from litestar.datastructures import State
from litestar.exceptions import ServiceUnavailableException

from flashcards_generator.delivery.web.auth_routes import auth_router
from flashcards_generator.delivery.web.schemas import HealthRead
from flashcards_generator.integrations.web_database import WebDatabase

__all__ = ["auth_router", "live", "ready"]


@get("/health/live", tags=["health"])
async def live() -> HealthRead:
    return HealthRead(status="ok")


@get("/health/ready", tags=["health"])
async def ready(state: State) -> HealthRead:
    database: WebDatabase = state.database
    try:
        await database.check()
    except Exception as error:
        raise ServiceUnavailableException(
            detail="Database schema is not ready"
        ) from error
    return HealthRead(status="ok")
