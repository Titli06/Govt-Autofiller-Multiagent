"""S3/MinIO storage service, exercised against a moto-mocked S3 (no real network/creds)."""

from __future__ import annotations

import pytest
from moto import mock_aws

from app.config import settings


@pytest.fixture(autouse=True)
def _s3_env(monkeypatch):
    # moto intercepts boto3 calls regardless of these creds, but boto3 requires *some*
    # credentials to be present to construct a client.
    monkeypatch.setattr(settings, "s3_access_key", "testing")
    monkeypatch.setattr(settings, "s3_secret_key", "testing")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")
    monkeypatch.setattr(settings, "s3_bucket", "govfill-test-bucket")
    monkeypatch.setattr(settings, "s3_endpoint_url", None)


@pytest.fixture()
def s3():
    with mock_aws():
        yield


def test_ensure_bucket_creates_when_missing(s3):
    from app.services import storage

    storage.ensure_bucket()
    # Idempotent — calling again must not raise.
    storage.ensure_bucket()


def test_put_get_delete_round_trip(s3):
    from app.services import storage

    storage.ensure_bucket()
    key = storage.put_document("user-123", b"raw image bytes", "image/jpeg")

    assert key.startswith("documents/user-123/")
    assert key.endswith(".jpg")

    fetched = storage.get_document(key)
    assert fetched == b"raw image bytes"

    storage.delete_document(key)
    with pytest.raises(Exception):
        storage.get_document(key)


def test_put_document_extension_by_content_type(s3):
    from app.services import storage

    storage.ensure_bucket()
    pdf_key = storage.put_document("user-1", b"%PDF-1.4", "application/pdf")
    heic_key = storage.put_document("user-1", b"heic-bytes", "image/heic")

    assert pdf_key.endswith(".pdf")
    assert heic_key.endswith(".heic")


def test_put_document_keys_are_unique_per_call(s3):
    from app.services import storage

    storage.ensure_bucket()
    key1 = storage.put_document("user-1", b"a", "image/png")
    key2 = storage.put_document("user-1", b"b", "image/png")
    assert key1 != key2
