"""PII-safe logging setup.

Phase 0 policy: log events and opaque ids (user_id) only — never email, password,
tokens, or cookie values (CLAUDE.md). We keep messages structured and value-free so raw
PII never reaches a sink; a richer redaction filter can be layered on in a later phase.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("govfill")


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent basic config; call once at app startup."""
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
