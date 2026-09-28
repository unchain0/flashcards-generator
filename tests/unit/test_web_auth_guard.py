from __future__ import annotations

import pytest
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.types import Scope

from flashcards_generator.delivery.web.auth import authenticated_user_id


def _connection(
    *,
    state: dict[str, str | int | list[str] | None] | None = None,
    headers: list[tuple[bytes, bytes]] | None = None,
    query_string: bytes = b"",
) -> ASGIConnection:
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/jobs/synthetic-job",
        "raw_path": b"/api/v1/jobs/synthetic-job",
        "query_string": query_string,
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("test.local", 80),
        "state": state or {},
    }
    return ASGIConnection(scope)


@pytest.mark.parametrize(
    "state",
    [
        None,
        {"auth_user_id": None},
        {"auth_user_id": 7},
        {"auth_user_id": ["user"]},
    ],
)
def test_authenticated_user_id_rejects_missing_or_non_text_identity(
    state: dict[str, str | int | list[str] | None] | None,
) -> None:
    with pytest.raises(NotAuthorizedException) as error:
        authenticated_user_id(_connection(state=state))

    assert error.value.status_code == 401
    assert error.value.detail == "Authentication required"


@pytest.mark.parametrize(
    ("headers", "query_string"),
    [
        ([(b"cookie", b"flashcards_session=synthetic-user")], b""),
        ([(b"authorization", b"Bearer synthetic-user")], b""),
        ([], b"auth_user_id=synthetic-user"),
    ],
)
def test_request_tokens_cannot_replace_guard_identity(
    headers: list[tuple[bytes, bytes]], query_string: bytes
) -> None:
    with pytest.raises(NotAuthorizedException) as error:
        authenticated_user_id(
            _connection(headers=headers, query_string=query_string)
        )

    assert error.value.status_code == 401
    assert error.value.detail == "Authentication required"


def test_authenticated_user_id_returns_guard_established_identity() -> None:
    assert (
        authenticated_user_id(
            _connection(state={"auth_user_id": "synthetic-user"})
        )
        == "synthetic-user"
    )
