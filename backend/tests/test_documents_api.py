"""Document upload/status/file endpoints. Storage (S3) and the Celery enqueue are mocked
— no real MinIO/Redis needed; only the route/ownership/validation logic is under test."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

EMAIL = "citizen@example.com"
PASSWORD = "supersecret1"


def _register_and_login(client, sent_emails, email=EMAIL, password=PASSWORD) -> dict:
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = sent_emails[-1]["token"]
    client.post("/api/auth/verify-email", json={"token": token})
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    access_token = r.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture(autouse=True)
def _mock_storage_and_task(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.documents.put_document", lambda user_id, data, content_type: "documents/fake-key.jpg"
    )
    monkeypatch.setattr(
        "app.api.routes.documents.get_document", lambda key: b"raw stored bytes"
    )
    mock_delay = MagicMock()
    monkeypatch.setattr("app.api.routes.documents.ocr_extract_task.delay", mock_delay)
    return mock_delay


def _upload(client, headers, doc_type="aadhaar", content=b"fake-jpeg-bytes", content_type="image/jpeg", filename="id.jpg"):
    return client.post(
        "/api/documents/upload",
        headers=headers,
        data={"doc_type": doc_type},
        files={"file": (filename, content, content_type)},
    )


def test_upload_requires_auth(client):
    r = _upload(client, headers={})
    assert r.status_code == 401


def test_upload_success_enqueues_task(client, sent_emails, _mock_storage_and_task):
    headers = _register_and_login(client, sent_emails)
    r = _upload(client, headers)
    assert r.status_code == 202
    body = r.json()
    assert body["ocr_status"] == "pending"
    assert body["document_id"]
    _mock_storage_and_task.assert_called_once_with(body["document_id"])


def test_upload_invalid_doc_type_rejected(client, sent_emails):
    headers = _register_and_login(client, sent_emails)
    r = _upload(client, headers, doc_type="passport")
    assert r.status_code == 422


def test_upload_unsupported_content_type_rejected(client, sent_emails):
    headers = _register_and_login(client, sent_emails)
    r = _upload(client, headers, content_type="application/zip", filename="id.zip")
    assert r.status_code == 415
    assert r.json()["detail"]["code"] == "UNSUPPORTED_TYPE"


def test_upload_file_too_large_rejected(client, sent_emails, monkeypatch):
    headers = _register_and_login(client, sent_emails)
    monkeypatch.setattr("app.config.settings.max_upload_bytes", 10)
    r = _upload(client, headers, content=b"x" * 1000)
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "FILE_TOO_LARGE"


def test_upload_enqueue_failure_marks_document_failed(client, sent_emails, monkeypatch):
    headers = _register_and_login(client, sent_emails)
    monkeypatch.setattr(
        "app.api.routes.documents.ocr_extract_task.delay",
        MagicMock(side_effect=RuntimeError("broker down")),
    )
    r = _upload(client, headers)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "ENQUEUE_FAILED"


def test_status_endpoint_returns_document(client, sent_emails):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    document_id = upload.json()["document_id"]

    r = client.get(f"/api/documents/{document_id}/status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == document_id
    assert body["declared_doc_type"] == "aadhaar"
    assert body["ocr_status"] == "pending"


def test_status_endpoint_unknown_document_404(client, sent_emails):
    headers = _register_and_login(client, sent_emails)
    r = client.get("/api/documents/00000000-0000-0000-0000-000000000000/status", headers=headers)
    assert r.status_code == 404


def test_status_endpoint_cross_user_returns_404(client, sent_emails):
    headers_a = _register_and_login(client, sent_emails, email="a@example.com")
    upload = _upload(client, headers_a)
    document_id = upload.json()["document_id"]

    headers_b = _register_and_login(client, sent_emails, email="b@example.com")
    r = client.get(f"/api/documents/{document_id}/status", headers=headers_b)
    assert r.status_code == 404


def test_file_endpoint_streams_raw_bytes(client, sent_emails):
    headers = _register_and_login(client, sent_emails)
    upload = _upload(client, headers)
    document_id = upload.json()["document_id"]

    r = client.get(f"/api/documents/{document_id}/file", headers=headers)
    assert r.status_code == 200
    assert r.content == b"raw stored bytes"


def test_file_endpoint_cross_user_returns_404(client, sent_emails):
    headers_a = _register_and_login(client, sent_emails, email="a@example.com")
    upload = _upload(client, headers_a)
    document_id = upload.json()["document_id"]

    headers_b = _register_and_login(client, sent_emails, email="b@example.com")
    r = client.get(f"/api/documents/{document_id}/file", headers=headers_b)
    assert r.status_code == 404
