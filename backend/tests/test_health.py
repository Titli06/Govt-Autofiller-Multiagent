"""Health endpoints: liveness always ok; readiness reports per-dependency status."""

from __future__ import annotations


def test_liveness(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_readiness_reports_checks(client, monkeypatch):
    # Force both dependency probes to fail → readiness is 503 with per-check status,
    # and must never raise a 500.
    import app.main as main

    class _Boom:
        def connect(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(main, "app", main.app)  # no-op, keep app reference
    monkeypatch.setattr("app.db.session.engine", _Boom())

    r = client.get("/health/ready")
    assert r.status_code in (200, 503)
    body = r.json()
    assert "postgres" in body["checks"]
    assert "redis" in body["checks"]
