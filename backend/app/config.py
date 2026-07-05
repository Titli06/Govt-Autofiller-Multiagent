"""Application settings, loaded from environment / .env via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"

    # Auth
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # PII field-level encryption
    pii_encryption_key: str = "change-me"

    # Infra
    database_url: str = "postgresql+psycopg://govfill:govfill@localhost:5432/govfill"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Object storage (S3 / MinIO)
    s3_endpoint_url: str | None = None
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "govfill-documents"
    s3_region: str = "us-east-1"

    # Vision-LLM
    anthropic_api_key: str = ""
    vision_model: str = "claude-opus-4-8"

    # Confidence policy — fields below this route to mandatory human review.
    confidence_threshold: float = 0.90


settings = Settings()
