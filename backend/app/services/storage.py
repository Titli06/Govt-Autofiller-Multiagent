"""S3-compatible object storage for raw document uploads (MinIO dev / AWS S3 prod).

Same boto3 API across environments — only the endpoint/credentials differ.
"""

# TODO: put_document(user_id, file) -> object_key ; get_document(key) ; delete_document(key)
