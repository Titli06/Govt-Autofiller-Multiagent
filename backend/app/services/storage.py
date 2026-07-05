"""S3-compatible object storage for raw document uploads (MinIO dev / AWS S3 prod).

Same boto3 API across environments — only the endpoint/credentials differ. All documents
live in one private bucket and are never public; the API streams them back through an
owner-authenticated endpoint instead. Retained until the Phase 5 cascade purge deletes
them (SPEC-PHASE1.md Decision 10).
"""

from __future__ import annotations

import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

# Bounded so an unreachable S3/MinIO endpoint fails fast (e.g. at app startup) rather than
# hanging on botocore's much longer defaults.
_CLIENT_CONFIG = Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 1})

_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
}


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=_CLIENT_CONFIG,
    )


def ensure_bucket() -> None:
    """Idempotent create-if-missing — a local/dev (MinIO) convenience. Safe to call at
    startup; in prod the bucket is expected to already exist and this is a no-op."""
    client = _client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        kwargs: dict = {"Bucket": settings.s3_bucket}
        if settings.s3_region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": settings.s3_region}
        client.create_bucket(**kwargs)


def put_document(user_id: str, data: bytes, content_type: str) -> str:
    """Stores the raw uploaded bytes and returns the object key."""
    ext = _EXTENSIONS.get(content_type, "")
    key = f"documents/{user_id}/{uuid.uuid4()}{ext}"
    _client().put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return key


def get_document(key: str) -> bytes:
    obj = _client().get_object(Bucket=settings.s3_bucket, Key=key)
    return obj["Body"].read()


def delete_document(key: str) -> None:
    _client().delete_object(Bucket=settings.s3_bucket, Key=key)
