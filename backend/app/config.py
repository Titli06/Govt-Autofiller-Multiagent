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

    # PII field-level encryption (Phase 1). Must be base64 of exactly 32 bytes (AES-256).
    pii_encryption_key: str = "change-me"

    # Uploads (Phase 1)
    max_upload_bytes: int = 10_485_760  # 10 MiB
    allowed_upload_content_types: list[str] = [
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
        "application/pdf",
    ]
    max_upload_pages: int = 10

    # OCR / extraction worker (Phase 1)
    ocr_max_retries: int = 3
    ocr_retry_backoff_seconds: int = 5
    ocr_confidence_high: float = 0.90
    ocr_confidence_medium: float = 0.70

    # Form fill worker (Phase 2)
    fill_max_retries: int = 3
    fill_retry_backoff_seconds: int = 5

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

    # Vision-LLM (Google Gemini) — used by services/ocr/vision_llm.py.
    gemini_api_key: str = ""
    vision_model: str = "gemini-2.5-flash"

    # Confidence policy — fields below this route to mandatory human review.
    confidence_threshold: float = 0.90


settings = Settings()
