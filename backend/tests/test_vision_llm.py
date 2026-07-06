"""vision_llm.extract: request/response shape and retryable-vs-terminal error
classification. app.services.ocr.vision_llm._client is patched with a fake client (no
real Gemini API calls)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from google.genai import errors

from app.config import settings
from app.services.ocr import vision_llm


def _fake_client(generate_content):
    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


def _returns(payload: dict):
    return lambda **_: SimpleNamespace(text=json.dumps(payload))


def _raises(exc: Exception):
    def _fn(**_):
        raise exc

    return _fn


def _api_error(cls, status_code: int, message: str = "error"):
    return cls(status_code, {"message": message, "status": message})


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")


def test_missing_api_key_raises_terminal_error(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
        vision_llm.extract([b"img"], "aadhaar")
    assert exc_info.value.transient is False


def test_unknown_doc_type_raises_terminal_error():
    with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
        vision_llm.extract([b"img"], "passport")
    assert exc_info.value.transient is False


def test_successful_extraction_parses_fields():
    payload = {
        "detected_doc_type": "aadhaar",
        "fields": {
            "full_name": {
                "present": True,
                "value": "Rajesh Kumar",
                "source_snippet": "Name: Rajesh Kumar",
                "self_confidence": 0.9,
            },
            "dob": {"present": False, "value": None, "source_snippet": None, "self_confidence": 0.0},
            "gender": {
                "present": True,
                "value": "Male",
                "source_snippet": "Gender: Male",
                "self_confidence": 0.8,
            },
            "aadhaar_number": {
                "present": True,
                "value": "234123412346",
                "source_snippet": "Aadhaar: 234123412346",
                "self_confidence": 0.95,
            },
            "address": {
                "present": True,
                "value": "123 MG Road",
                "source_snippet": "Address: 123 MG Road",
                "self_confidence": 0.7,
            },
        },
    }
    with patch.object(vision_llm, "_client", return_value=_fake_client(_returns(payload))):
        result = vision_llm.extract([b"img"], "aadhaar")

    assert result.detected_doc_type == "aadhaar"
    assert result.fields["full_name"].value == "Rajesh Kumar"
    assert result.fields["full_name"].present is True
    assert result.fields["dob"].present is False


def test_empty_response_raises_terminal_error():
    client = _fake_client(lambda **_: SimpleNamespace(text=""))
    with patch.object(vision_llm, "_client", return_value=client):
        with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
            vision_llm.extract([b"img"], "aadhaar")
    assert exc_info.value.transient is False


def test_invalid_json_raises_terminal_error():
    client = _fake_client(lambda **_: SimpleNamespace(text="not json"))
    with patch.object(vision_llm, "_client", return_value=client):
        with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
            vision_llm.extract([b"img"], "aadhaar")
    assert exc_info.value.transient is False


def test_server_error_is_transient():
    client = _fake_client(_raises(_api_error(errors.ServerError, 500)))
    with patch.object(vision_llm, "_client", return_value=client):
        with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
            vision_llm.extract([b"img"], "aadhaar")
    assert exc_info.value.transient is True


def test_rate_limit_client_error_is_transient():
    client = _fake_client(_raises(_api_error(errors.ClientError, 429)))
    with patch.object(vision_llm, "_client", return_value=client):
        with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
            vision_llm.extract([b"img"], "aadhaar")
    assert exc_info.value.transient is True


def test_bad_request_client_error_is_terminal():
    client = _fake_client(_raises(_api_error(errors.ClientError, 400)))
    with patch.object(vision_llm, "_client", return_value=client):
        with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
            vision_llm.extract([b"img"], "aadhaar")
    assert exc_info.value.transient is False


def test_authentication_client_error_is_terminal():
    client = _fake_client(_raises(_api_error(errors.ClientError, 401)))
    with patch.object(vision_llm, "_client", return_value=client):
        with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
            vision_llm.extract([b"img"], "aadhaar")
    assert exc_info.value.transient is False


def test_transport_error_is_transient():
    client = _fake_client(_raises(httpx.ConnectError("connection refused")))
    with patch.object(vision_llm, "_client", return_value=client):
        with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
            vision_llm.extract([b"img"], "aadhaar")
    assert exc_info.value.transient is True


# --- classify_form (Phase 2) ---------------------------------------------------------


def test_classify_form_returns_known_type():
    payload = {"form_type": "income_certificate"}
    with patch.object(vision_llm, "_client", return_value=_fake_client(_returns(payload))):
        result = vision_llm.classify_form([b"img"], ["income_certificate", "scholarship_application"])
    assert result == "income_certificate"


def test_classify_form_returns_unknown_when_model_unsure():
    payload = {"form_type": "unknown"}
    with patch.object(vision_llm, "_client", return_value=_fake_client(_returns(payload))):
        result = vision_llm.classify_form([b"img"], ["income_certificate"])
    assert result == "unknown"


def test_classify_form_missing_field_defaults_to_unknown():
    with patch.object(vision_llm, "_client", return_value=_fake_client(_returns({}))):
        result = vision_llm.classify_form([b"img"], ["income_certificate"])
    assert result == "unknown"


def test_classify_form_server_error_is_transient():
    client = _fake_client(_raises(_api_error(errors.ServerError, 500)))
    with patch.object(vision_llm, "_client", return_value=client):
        with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
            vision_llm.classify_form([b"img"], ["income_certificate"])
    assert exc_info.value.transient is True


def test_classify_form_bad_request_is_terminal():
    client = _fake_client(_raises(_api_error(errors.ClientError, 400)))
    with patch.object(vision_llm, "_client", return_value=client):
        with pytest.raises(vision_llm.VisionExtractionError) as exc_info:
            vision_llm.classify_form([b"img"], ["income_certificate"])
    assert exc_info.value.transient is False
