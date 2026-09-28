from __future__ import annotations

import pytest
from litestar import Litestar
from litestar.testing import AsyncTestClient

from tests.integration.companion_generation_support import (
    WorkflowStub,
    auth_headers,
    successful_generation,
    wait_for_terminal_job,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_generation_job_and_artifact_are_private_to_the_owner(
    companion: tuple[AsyncTestClient[Litestar], WorkflowStub],
) -> None:
    client, workflow = companion
    workflow.generate_operation = successful_generation
    response = await client.post(
        "/v1/jobs",
        headers=auth_headers(),
        files=[("files", ("lesson.pdf", b"%PDF-1.7", "application/pdf"))],
    )
    job = await wait_for_terminal_job(client, response.json()["id"])
    job_id = str(job["id"])
    artifact_url = str(job["artifacts"][0]["url"])

    other_job = await client.get(
        f"/v1/jobs/{job_id}", headers=auth_headers("other-capability")
    )
    other_artifact = await client.get(
        artifact_url, headers=auth_headers("other-capability")
    )

    assert other_job.status_code == 404
    assert other_artifact.status_code == 404
