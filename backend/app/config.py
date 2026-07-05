"""Application settings, loaded from environment / .env via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"

    # Auth — access token (JWT) + DB-backed rotating refresh token.
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    email_verification_expire_hours: int = 24

    # Refresh cookie (httpOnly; browser JS can't read it).
    refresh_cookie_name: str = "refresh_token"
    refresh_cookie_secure: bool = False  # True in prod (HTTPS)
    refresh_cookie_samesite: str = "lax"
    refresh_cookie_path: str = "/api/auth"  # cookie only sent to auth endpoints

    # PII field-level encryption — unused until Phase 1.
    pii_encryption_key: str = "change-me"

    # Infra
    database_url: str = "postgresql+psycopg://govfill:govfill@localhost:5432/govfill"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # SMTP (Mailpit in dev; real provider creds in prod — same code).
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    mail_from: str = "no-reply@govfill.local"

    # URLs
    frontend_base_url: str = "http://localhost:5173"  # used to build verification links
    cors_origins: list[str] = []  # empty in dev (same-origin via Vite proxy)

    # Object storage (S3 / MinIO) — unused until Phase 1.
    s3_endpoint_url: str | None = None
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "govfill-documents"
    s3_region: str = "us-east-1"

    # Vision-LLM — unused until Phase 1.
    anthropic_api_key: str = ""
    vision_model: str = "claude-opus-4-8"

    # Confidence policy — fields below this route to mandatory human review.
    confidence_threshold: float = 0.90


settings = Settings()
