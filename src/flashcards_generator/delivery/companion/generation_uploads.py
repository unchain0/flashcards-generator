from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import anyio
from litestar.datastructures import UploadFile
from litestar.exceptions import HTTPException

from flashcards_generator.delivery.companion.generation_jobs import (
    MAX_JOB_FILES,
    MAX_JOB_UPLOAD_BYTES,
    UPLOAD_CHUNK_BYTES,
)
from flashcards_generator.integrations.document_limits import (
    MAX_PDF_FILE_BYTES,
)

_ALLOWED_SUFFIXES = frozenset({".pdf", ".pptx"})


async def save_uploads(
    input_dir: Path,
    uploads: list[UploadFile],
    filenames: list[str],
) -> None:
    total_bytes = 0
    for upload, filename in zip(uploads, filenames, strict=True):
        file_bytes = 0
        path = input_dir / filename
        async with await anyio.open_file(path, "xb") as output:
            while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
                file_bytes += len(chunk)
                total_bytes += len(chunk)
                if _exceeds_upload_limits(file_bytes, total_bytes):
                    raise HTTPException(
                        detail="Os arquivos excedem o limite de tamanho permitido.",
                        status_code=413,
                    )
                await output.write(chunk)
        if file_bytes == 0:
            raise HTTPException(
                detail="Não é possível gerar flashcards com um arquivo vazio.",
                status_code=400,
            )


def _exceeds_upload_limits(file_bytes: int, total_bytes: int) -> bool:
    return (
        file_bytes > MAX_PDF_FILE_BYTES or total_bytes > MAX_JOB_UPLOAD_BYTES
    )


def safe_filenames(uploads: list[UploadFile]) -> list[str]:
    if not uploads or len(uploads) > MAX_JOB_FILES:
        raise HTTPException(
            detail=f"Envie de 1 a {MAX_JOB_FILES} arquivos PDF ou PPTX.",
            status_code=400,
        )
    return [
        _safe_filename(index, upload)
        for index, upload in enumerate(uploads, start=1)
    ]


def _safe_filename(index: int, upload: UploadFile) -> str:
    original = upload.filename
    name = PurePosixPath((original or "").replace("\\", "/")).name
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            detail="Aceitamos somente arquivos PDF ou PPTX.",
            status_code=400,
        )
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(name).stem).strip("-_")
    return f"{index:02d}-{stem[:80] or 'document'}{suffix}"
