"""Document routes: upload ID docs (drag-drop / camera), triggers async OCR extraction."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned_document
from app.config import settings
from app.core.logging import logger
from app.models.document import Document
from app.models.user import User
from app.schemas.document import DocumentStatusResponse, DocumentUploadResponse
from app.services.storage import get_document, put_document
from app.workers.tasks import ocr_extract_task

router = APIRouter()

_READ_CHUNK_BYTES = 1024 * 1024


def _err(status_code: int, detail: str, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"detail": detail, "code": code})


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Reads the upload in chunks, aborting as soon as it exceeds max_bytes rather than
    buffering an arbitrarily large file first."""
    data = bytearray()
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > max_bytes:
            raise _err(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large", "FILE_TOO_LARGE")
    return bytes(data)


@router.post(
    "/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED
)
async def upload_document(
    file: UploadFile = File(...),
    doc_type: Literal["aadhaar", "pan"] = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentUploadResponse:
    content_type = file.content_type or ""
    if content_type not in settings.allowed_upload_content_types:
        raise _err(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported file type", "UNSUPPORTED_TYPE")

    data = await _read_capped(file, settings.max_upload_bytes)

    s3_key = put_document(str(user.id), data, content_type)
    doc = Document(
        user_id=user.id,
        declared_doc_type=doc_type,
        s3_key=s3_key,
        content_type=content_type,
        byte_size=len(data),
        ocr_status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    try:
        ocr_extract_task.delay(str(doc.id))
    except Exception:
        doc.ocr_status = "failed"
        doc.ocr_error = "failed to enqueue extraction job"
        db.commit()
        logger.error("document_upload enqueue_failed document_id=%s", doc.id)
        raise _err(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Failed to enqueue extraction job",
            "ENQUEUE_FAILED",
        )

    logger.info("document_upload document_id=%s user_id=%s", doc.id, user.id)
    return DocumentUploadResponse(document_id=doc.id, ocr_status=doc.ocr_status)


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
def document_status(doc: Document = Depends(get_owned_document)) -> DocumentStatusResponse:
    return DocumentStatusResponse.model_validate(doc)


@router.get("/{document_id}/file")
def document_file(doc: Document = Depends(get_owned_document)) -> Response:
    data = get_document(doc.s3_key)
    return Response(content=data, media_type=doc.content_type or "application/octet-stream")
