"""End-to-end auth flow tests: register → verify → login → refresh/rotate → logout,
plus the security edge cases (enumeration safety, refresh-reuse family revocation)."""

from __future__ import annotations

EMAIL = "citizen@example.com"
PASSWORD = "supersecret1"


def _register(client, email=EMAIL, password=PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _verify(client, token):
    return client.post("/api/auth/verify-email", json={"token": token})


def _register_and_verify(client, sent_emails, email=EMAIL, password=PASSWORD):
    _register(client, email, password)
    token = sent_emails[-1]["token"]
    _verify(client, token)


def _login(client, email=EMAIL, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# --- Registration + verification --------------------------------------------


def test_register_creates_unverified_user_and_sends_email(client, sent_emails):
    r = _register(client)
    assert r.status_code == 201
    assert len(sent_emails) == 1
    assert sent_emails[0]["token"]


def test_register_normalizes_email_case(client, sent_emails):
    assert _register(client, email="MixedCase@Example.com").status_code == 201
    # Same address, different case → treated as the existing unverified account (re-send).
    r = _register(client, email="mixedcase@example.com")
    assert r.status_code == 201


def test_duplicate_verified_email_conflicts(client, sent_emails):
    _register_and_verify(client, sent_emails)
    r = _register(client)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "EMAIL_TAKEN"


def test_password_too_short_is_rejected(client):
    r = _register(client, password="short")
    assert r.status_code == 422


def test_verify_with_bad_token_fails(client):
    r = _verify(client, "not-a-real-token")
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_OR_EXPIRED_TOKEN"


def test_verify_token_is_single_use(client, sent_emails):
    _register(client)
    token = sent_emails[-1]["token"]
    assert _verify(client, token).status_code == 200
    # Replaying the consumed token is rejected (single-use guard fires before any
    # already-verified check, so the code is INVALID_OR_EXPIRED_TOKEN).
    r = _verify(client, token)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_OR_EXPIRED_TOKEN"


def test_resend_invalidates_prior_verification_token(client, sent_emails):
    _register(client)
    first = sent_emails[-1]["token"]
    client.post("/api/auth/resend-verification", json={"email": EMAIL})
    second = sent_emails[-1]["token"]
    assert first != second
    # The superseded token no longer works; the freshly issued one does.
    assert _verify(client, first).status_code == 400
    assert _verify(client, second).status_code == 200


# --- Login blocked until verified -------------------------------------------


def test_login_blocked_before_verification(client, sent_emails):
    _register(client)
    r = _login(client)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "EMAIL_NOT_VERIFIED"


def test_login_succeeds_after_verification(client, sent_emails):
    _register_and_verify(client, sent_emails)
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"]
    assert body["user"]["email"] == EMAIL
    # Refresh cookie is set httpOnly.
    assert client.cookies.get("refresh_token") is not None


def test_login_wrong_password_is_generic_401(client, sent_emails):
    _register_and_verify(client, sent_emails)
    r = _login(client, password="wrongpassword")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_login_unknown_email_matches_wrong_password_response(client):
    # Enumeration safety: unknown email and wrong password look identical.
    r = _login(client, email="nobody@example.com", password="whatever12")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_resend_verification_is_generic_for_unknown_email(client):
    r = client.post("/api/auth/resend-verification", json={"email": "ghost@example.com"})
    assert r.status_code == 200


# --- /me ---------------------------------------------------------------------


def test_me_requires_and_returns_user(client, sent_emails):
    _register_and_verify(client, sent_emails)
    token = _login(client).json()["access_token"]
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == EMAIL


def test_me_rejects_missing_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_me_rejects_garbage_token(client):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


# --- Refresh rotation + reuse detection -------------------------------------


def test_refresh_rotates_and_issues_new_access_token(client, sent_emails):
    _register_and_verify(client, sent_emails)
    _login(client)
    old_cookie = client.cookies.get("refresh_token")

    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    assert r.json()["access_token"]
    new_cookie = client.cookies.get("refresh_token")
    assert new_cookie is not None and new_cookie != old_cookie


def test_refresh_reuse_revokes_family(client, sent_emails):
    _register_and_verify(client, sent_emails)
    _login(client)
    stolen = client.cookies.get("refresh_token")

    # Legit rotation — client now holds a new cookie.
    assert client.post("/api/auth/refresh").status_code == 200

    # Replay the stolen (now-rotated) token → reuse detected, family revoked.
    r = client.post("/api/auth/refresh", cookies={"refresh_token": stolen})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_REFRESH"

    # The current (descendant) token is now revoked too — whole family is dead.
    r2 = client.post("/api/auth/refresh")
    assert r2.status_code == 401


def test_refresh_without_cookie_is_401(client):
    r = client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_REFRESH"


# --- Logout ------------------------------------------------------------------


def test_logout_revokes_and_is_idempotent(client, sent_emails):
    _register_and_verify(client, sent_emails)
    _login(client)
    assert client.post("/api/auth/logout").status_code == 204
    # The revoked refresh token can no longer be rotated.
    assert client.post("/api/auth/refresh").status_code == 401
    # Logging out again with no session still succeeds (idempotent).
    assert client.post("/api/auth/logout").status_code == 204
