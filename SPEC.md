# SPEC — Phase 0: Walking Skeleton (Auth + App Shell)

Scope-locked spec for **Phase 0 only** of [PLAN.md](PLAN.md). Ships one end-to-end vertical
slice — DB → backend → frontend → infra — with **no product feature**: a user can register,
verify their email, log in, and land on an empty authed dashboard. Every later phase hangs its
data off the `User` this phase creates.

> Authority: [PLAN.md](PLAN.md) Phase 0 + [govform-autofiller-prd.md](govform-autofiller-prd.md)
> §5.9 (JWT+bcrypt), §8 (security/privacy NFRs), [CLAUDE.md](CLAUDE.md) (no raw-PII logging).
> Where PLAN and PRD are silent, the decisions below were made in the Phase 0 build interview
> and are binding for this phase.

---

## 1. Objectives & Done-When

**Done when:** a user can register → receive a verification email (via Mailpit in dev) → verify →
log in → land on an authed, empty dashboard with working route protection; and
`docker compose up` brings **postgres + redis + minio + api + worker + frontend** up with
`/health` (liveness) and `/health/ready` (DB+Redis) green.

Acceptance is enumerated in §11.

### In scope
- `User` model + `RefreshToken` + `EmailVerificationToken` tables; first Alembic migration; sync DB session wiring.
- `core/security.py`: bcrypt hashing, access-token issue/verify, refresh-token + verification-token hashing helpers.
- Auth endpoints: `register`, `verify-email`, `resend-verification`, `login`, `refresh`, `logout`, `me`.
- `get_current_user` dependency (+ `get_db`).
- Email delivery service (SMTP → Mailpit in dev) and verification-email template.
- `/health` (liveness) + `/health/ready` (readiness: DB + Redis).
- Frontend: register/login/verify pages, in-memory access token + silent refresh, `api/client.ts`, protected app shell routing Upload/Review/History (empty placeholders).
- Infra: backend + frontend Dockerfiles, Vite dev proxy, Mailpit service, Postgres/Redis healthchecks, migration-on-start, celery worker boots clean.

### Out of scope (defer to later phases / later)
- Any document/profile/form/OCR/agent functionality — routers stay as stubs; pages are empty placeholders.
- Password reset / "forgot password" flow (not in Phase 0; add when needed).
- OAuth/SSO, MFA, account lockout / rate limiting (note as future hardening in §10).
- Field-level PII encryption (`core/encryption.py`) — Phase 1.
- Direct cross-origin CORS deployment topology — dev uses a same-origin Vite proxy; prod reverse-proxy topology is a later concern.

---

## 2. Decisions carried from the interview (binding for Phase 0)

| Area | Decision |
|---|---|
| Session model | **DB-backed refresh tokens with rotation + reuse detection.** Short-lived access JWT, long-lived opaque refresh token stored **hashed** in DB; rotate on every `/refresh`; revocable on logout. |
| Frontend token storage | **httpOnly refresh cookie** (browser JS cannot read it) + **access token held in memory** (React state). On page load the app silently calls `/refresh` to rehydrate the session. |
| Registration | **Email verification required.** Register → email a verification link → user must verify before login works. |
| Unverified login | **Blocked.** `login` returns `403 EMAIL_NOT_VERIFIED` (with a resend affordance); no tokens issued until verified. |
| Dev email | **Mailpit** container in `docker-compose`; catches all outbound mail, web UI at `:8025`. Prod swaps real SMTP creds via env — no code change. |
| Cross-origin | **Vite dev proxy**: frontend `:5173` proxies `/api` → api `:8000`, so the browser sees one origin. Refresh cookie stays `SameSite=Lax`; no CORS credential dance in dev. |
| Health | **Split**: `/health` = liveness (always `ok`); `/health/ready` = readiness (pings Postgres + Redis, `503` if either down). |

### Default implementation choices (not interviewed; set here)
- **Password hashing:** bcrypt via `passlib` (already a dep). Min password length 8; reject > 72 bytes (bcrypt limit) with a clear error.
- **Access token:** JWT (HS256), `access_token_expire_minutes = 15`. Claims: `sub` (user id, str UUID), `type: "access"`, `iat`, `exp`, `jti`.
- **Refresh token:** opaque 256-bit random (`secrets.token_urlsafe(32)`), lifetime `refresh_token_expire_days = 30`, stored as SHA-256 hash. Rotated on use; old token marked `replaced_by`.
- **Verification token:** opaque 256-bit random, stored SHA-256-hashed, `email_verification_expire_hours = 24`, single-use (`used_at`).
- **DB access:** synchronous SQLAlchemy 2.0 (`Session`/`sessionmaker`) to stay consistent with sync Celery workers; `psycopg` v3 sync driver (matches `postgresql+psycopg://` in config).
- **IDs:** UUIDv4 primary keys (`sub` claim is opaque; avoids leaking row counts).
- **Timestamps:** timezone-aware UTC (`TIMESTAMP WITH TIME ZONE`), server-default `now()`.

---

## 3. Data Model

Three tables. First Alembic migration creates all three. `db/base.py` exposes `Base`
(`DeclarativeBase`) and imports every model so autogenerate sees all tables.

### 3.1 `users` (`models/user.py`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | default uuid4 |
| `email` | text, **unique**, not null | stored **lowercased**; unique index on `lower(email)` (or normalize on write) |
| `password_hash` | text, not null | bcrypt |
| `email_verified_at` | timestamptz, nullable | null ⇒ unverified; non-null ⇒ verified-at instant |
| `created_at` | timestamptz, not null | server default now() |
| `updated_at` | timestamptz, not null | on-update now() |

Helper property `is_verified` = `email_verified_at is not None`. **Never** log `email` or `password_hash` (CLAUDE.md).

### 3.2 `refresh_tokens` (`models/refresh_token.py`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → users.id | `ON DELETE CASCADE` |
| `token_hash` | text, **unique**, not null | SHA-256 of the opaque token; raw token never stored |
| `family_id` | UUID, not null, indexed | rotation lineage; a reuse anywhere in the family revokes the whole family |
| `expires_at` | timestamptz, not null | |
| `revoked_at` | timestamptz, nullable | set on logout or reuse-detection |
| `replaced_by` | UUID FK → refresh_tokens.id, nullable | points at the rotated successor |
| `created_at` | timestamptz, not null | |

### 3.3 `email_verification_tokens` (`models/email_verification_token.py`)
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → users.id | `ON DELETE CASCADE` |
| `token_hash` | text, **unique**, not null | SHA-256 of opaque token |
| `expires_at` | timestamptz, not null | now + 24h |
| `used_at` | timestamptz, nullable | single-use guard |
| `created_at` | timestamptz, not null | |

Issuing a new verification token **invalidates prior unused tokens** for that user (mark them used, or delete). Cascade deletes here are the seed of the Phase 5 purge story.

---

## 4. Config & Env additions

`config.py` gains (with `.env.example` mirrored):

```
access_token_expire_minutes: int = 15
refresh_token_expire_days: int = 30
email_verification_expire_hours: int = 24

# Cookie
refresh_cookie_name: str = "refresh_token"
refresh_cookie_secure: bool = False        # True in prod (HTTPS)
refresh_cookie_samesite: str = "lax"
refresh_cookie_path: str = "/api/auth"     # cookie only sent to auth endpoints

# SMTP (Mailpit in dev)
smtp_host: str = "mailpit"
smtp_port: int = 1025
smtp_user: str = ""
smtp_password: str = ""
smtp_use_tls: bool = False
mail_from: str = "no-reply@govfill.local"

# URLs
frontend_base_url: str = "http://localhost:5173"   # used to build verification links
cors_origins: list[str] = []                        # empty in dev (same-origin via proxy)
```

`.env.example` updates: add the SMTP/cookie/token vars above; the existing `jwt_expire_minutes`
is superseded by `access_token_expire_minutes` (keep or remove — remove to avoid confusion). Add
a note that `PII_ENCRYPTION_KEY` is unused until Phase 1.

---

## 5. `core/security.py` (backend)

Pure functions, no DB access (DB is the caller's job):

- `hash_password(pw: str) -> str` — bcrypt.
- `verify_password(pw: str, pw_hash: str) -> bool`.
- `create_access_token(user_id: str) -> str` — HS256, claims per §2, `exp = now + access_token_expire_minutes`.
- `decode_access_token(token: str) -> dict` — verify sig+exp, assert `type == "access"`; raise on invalid/expired.
- `generate_opaque_token() -> str` — `secrets.token_urlsafe(32)`.
- `hash_token(raw: str) -> str` — SHA-256 hex (for refresh + verification tokens).

Timing-safe comparison via hashing + unique-index lookup (we look tokens up by their hash, so lookups are constant-work). Login must **always run a bcrypt verify** (dummy hash for unknown email) to avoid user-enumeration via timing.

---

## 6. API surface (`api/routes/auth.py`, `api/deps.py`, `main.py`)

All under `/api/auth`. JSON in/out unless noted. Errors use a consistent shape
`{"detail": <str>, "code": <MACHINE_CODE>}`.

### 6.1 Dependencies (`deps.py`)
- `get_db()` — yields a sync `Session`, closes in `finally`.
- `get_current_user(authorization: Header, db)` — parse `Bearer` access token → `decode_access_token` → load user → 401 (`code: INVALID_TOKEN` / `TOKEN_EXPIRED`) on any failure. Returns `User`.

### 6.2 Endpoints

| Method | Path | Auth | Body | Success | Key errors |
|---|---|---|---|---|---|
| POST | `/register` | none | `{email, password}` | `201 {message}` (no tokens) | `409 EMAIL_TAKEN` (verified email exists); `422` validation |
| POST | `/verify-email` | none | `{token}` | `200 {message}` | `400 INVALID_OR_EXPIRED_TOKEN`; `400 ALREADY_VERIFIED` |
| POST | `/resend-verification` | none | `{email}` | `200 {message}` (generic — see §10) | — |
| POST | `/login` | none | `{email, password}` | `200 {access_token, token_type, user}` + **Set-Cookie** refresh | `401 INVALID_CREDENTIALS`; `403 EMAIL_NOT_VERIFIED` |
| POST | `/refresh` | refresh cookie | — | `200 {access_token, token_type}` + **Set-Cookie** rotated refresh | `401 INVALID_REFRESH` (missing/expired/revoked/reused) |
| POST | `/logout` | refresh cookie | — | `204` + clear cookie | idempotent — `204` even if no/invalid cookie |
| GET | `/me` | access token | — | `200 {id, email, email_verified_at, created_at}` | `401` |

**Behavioral rules:**
- **Register** normalizes email to lowercase. If email exists **and verified** → `409 EMAIL_TAKEN`. If exists **and unverified** → treat as idempotent re-send: (re)issue a verification token + email, return `201` with the same generic message (don't leak which case occurred beyond the taken/verified split). Password validated (len 8–72 bytes). Creates `User` with `email_verified_at = NULL`, issues verification token, sends email with `{frontend_base_url}/verify?token=<raw>`.
- **verify-email** looks up by `hash_token(token)`; reject if not found / expired / already used; on success set `user.email_verified_at = now()`, mark token `used_at`. If the user is already verified and token unused, return `400 ALREADY_VERIFIED` (or `200` idempotent — pick `200` if the same token is replayed within its window; treat truly reused token as `400`). Keep it simple: not-found/expired/used → `400 INVALID_OR_EXPIRED_TOKEN`.
- **login** loads user by lowercased email; always runs bcrypt verify (dummy on miss); on bad creds → `401 INVALID_CREDENTIALS` (generic, no enumeration). If creds ok but `not is_verified` → `403 EMAIL_NOT_VERIFIED`. On success: issue access token (body) + create a **new refresh family** (fresh `family_id`), set httpOnly cookie.
- **refresh**: read cookie → `hash_token` → look up row.
  - Not found / expired → `401`.
  - **Revoked or already-replaced (reuse detected)** → revoke the **entire `family_id`**, return `401 INVALID_REFRESH`. (Stolen-token defense.)
  - Valid → mint new refresh token in the **same family**, set `replaced_by` on the old row, `revoked_at = now()` on old, set new cookie, return new access token.
- **logout**: if cookie present and valid, revoke that token (and optionally its family). Always clear the cookie, always `204`.

**Cookie attributes** (set on login + refresh, cleared on logout):
`HttpOnly; SameSite=Lax; Path=/api/auth; Max-Age=<refresh days>; Secure=<config, false in dev>`.

### 6.3 Response schemas (`schemas/auth.py`, `schemas/user.py`)
- `RegisterRequest {email: EmailStr, password: str}` (password constrained 8–72).
- `LoginRequest {email, password}`.
- `VerifyEmailRequest {token: str}`; `ResendRequest {email: EmailStr}`.
- `TokenResponse {access_token: str, token_type: "bearer", user: UserOut}` (login) / `{access_token, token_type}` (refresh).
- `UserOut {id: UUID, email: str, email_verified_at: datetime | None, created_at: datetime}`.

---

## 7. Email service (`services/email.py` + template)

- `send_verification_email(to: str, verify_url: str)` — builds a MIME message, sends via SMTP
  (`smtp_host:smtp_port`, TLS per config). In dev → Mailpit (`mailpit:1025`), viewable at `:8025`.
- Sending happens **inline** in Phase 0 (acceptable for a walking skeleton). Note in code that
  Phase 1+ can move it onto Celery (`send_email_task`) for retry durability — do **not** build the
  Celery task now, just leave the seam.
- Template: minimal HTML + plaintext ("Verify your GovFill account", one button/link to
  `verify_url`, states the link expires in 24h). No PII beyond the address it's sent to.

---

## 8. Health (`main.py`)

- `GET /health` → `200 {"status": "ok"}` — liveness, no dependency calls.
- `GET /health/ready` → checks:
  - Postgres: `SELECT 1` on a short-lived connection.
  - Redis: `PING` against `celery_broker_url`.
  - `200 {"status":"ready","checks":{"postgres":"ok","redis":"ok"}}` if both ok;
    `503` with per-check status otherwise. Never raises 500.

---

## 9. Infra (`docker-compose.yml`, Dockerfiles)

**New files:**
- `backend/Dockerfile` — `python:3.11-slim`; install build deps for `psycopg`/`bcrypt` as needed; `pip install -e ".[dev]"`; default CMD irrelevant (compose overrides).
- `backend/entrypoint.sh` — run `alembic upgrade head` then exec the passed command (uvicorn). Ensures schema exists before the API serves. Worker uses the same image but skips migration (or waits — only the `api` service runs migrations to avoid races).
- `frontend/Dockerfile` — `node:20-alpine`; `npm install`; CMD `npm run dev -- --host 0.0.0.0`.

**`docker-compose.yml` changes:**
- Add **`mailpit`** service (`axllent/mailpit`), ports `1025` (SMTP) + `8025` (UI).
- Add **healthchecks** to `postgres` (`pg_isready`) and `redis` (`redis-cli ping`).
- `api` `depends_on` postgres/redis **with `condition: service_healthy`**; `command` runs through `entrypoint.sh`.
- `worker` `depends_on` the same; boots `celery -A app.workers.celery_app worker` and must start clean (no tasks needed yet — Phase 0 only proves it connects to Redis).
- `frontend` depends_on `api`.
- `api`/`worker` get `mailpit` in `depends_on` (worker optional since email is inline in api for now).

**Celery (`workers/celery_app.py`):** minimal Celery app reading broker/result from settings so the worker boots and connects. No tasks registered in Phase 0 (tasks.py stays a stub). "Worker up + connected to Redis" is the Phase 0 bar.

---

## 10. Security & edge cases (must-handle)

- **No raw PII in logs** (CLAUDE.md): never log email, password, tokens, cookie values. Log user by `id` only.
- **User enumeration:** `login` returns identical `401 INVALID_CREDENTIALS` for unknown-email vs wrong-password, and always performs a bcrypt verify. `resend-verification` returns a **generic** `200` regardless of whether the email exists or is already verified (don't reveal account existence).
- **Refresh-token theft:** reuse of a rotated/revoked refresh token revokes the whole `family_id` → attacker + victim both forced to re-login. This is the point of DB-backed rotation.
- **Token opacity:** refresh + verification tokens are random opaque strings stored **hashed**; a DB read cannot reconstruct a usable token.
- **Cookie scope:** refresh cookie is `HttpOnly`, `Path=/api/auth` (not sent on non-auth requests), `SameSite=Lax`, `Secure` in prod.
- **Password bounds:** reject < 8 chars and > 72 bytes (bcrypt truncation) with clear `422`.
- **Expired verification link:** `400 INVALID_OR_EXPIRED_TOKEN`; frontend `/verify` page shows a "resend" affordance.
- **Double-verify / replayed link:** used token → `400`; already-verified user → clear message, route to login.
- **Access-token expiry mid-session:** client gets `401`, transparently calls `/refresh` once, retries; if refresh also fails → redirect to login (see §11 frontend).
- **Deferred hardening (NOT built in Phase 0, noted for later):** rate limiting / account lockout on login + resend, password reset, CSRF token for cookie-bearing POSTs (mitigated in dev by `SameSite=Lax` + same-origin proxy; revisit for prod cross-origin), email-send retry via Celery.

---

## 11. Frontend (`frontend/src`)

**New scaffolding this phase must add** (absent today): `index.html`, `src/main.tsx`,
`vite.config.ts` (with `/api` proxy → `http://api:8000` in compose / `localhost:8000` local),
`tsconfig.json` + `tsconfig.node.json`, `eslint` config.

**Vite proxy** (`vite.config.ts`): proxy `/api` → backend, `changeOrigin`, forward cookies — so
the browser treats API as same-origin and the httpOnly cookie "just works".

**Auth state:** an `AuthContext` holding `{ accessToken (in memory), user, status }`.
- On app mount: call `POST /api/auth/refresh`. If `200`, store access token + fetch `/me` → authed.
  If `401`, unauthed → login. (This is how an in-memory access token survives reloads.)

**`api/client.ts`:** `fetch` wrapper, base `/api`, `credentials: "include"`, attaches
`Authorization: Bearer <accessToken>` from context. On `401`: attempt one `/refresh`; on success
retry the original request with the new token; on failure clear auth + redirect to `/login`.
Typed methods for Phase 0: `register`, `login`, `logout`, `refresh`, `me`, `verifyEmail`,
`resendVerification`. (Document/form methods deferred.)

**Routing** (`App.tsx`, react-router):
- Public: `/login`, `/register`, `/verify` (reads `?token=`, calls `verifyEmail`, shows success/expired + link to login).
- `ProtectedRoute` wrapper: unauthed → redirect `/login`.
- Protected shell (nav + logout button): `/` (empty dashboard), `/upload`, `/review`, `/history` — all **empty placeholders** this phase.
- After register → "check your email" screen. After successful verify → login. After login → `/`.
- If login returns `403 EMAIL_NOT_VERIFIED` → show "verify your email" with a **resend** button.

**Pages:** minimal, unstyled-but-usable. `Upload.tsx`/`Review.tsx`/`History.tsx` render a
placeholder heading only.

---

## 12. Testing (`backend/tests`)

Phase 0 tests (pytest + httpx `TestClient`, SQLite or a test Postgres per `conftest.py`):
- `hash_password`/`verify_password` round-trip; wrong password fails.
- Access token create → decode round-trip; expired/tampered token rejected; wrong `type` rejected.
- **Register** creates unverified user + issues verification token + "sends" email (assert via a captured/mock mailer); duplicate verified email → `409`.
- **Login blocked** while unverified → `403 EMAIL_NOT_VERIFIED`; succeeds after verify → returns access token + sets refresh cookie.
- **verify-email**: valid token verifies; expired/used/unknown → `400`.
- **refresh**: valid cookie rotates (old revoked, new works); **reused old token revokes family** → subsequent refresh `401`.
- **logout** clears cookie + revokes; idempotent.
- `get_current_user`: valid token → user; missing/invalid/expired → `401`.
- **enumeration:** unknown-email login and wrong-password login return identical status/body; `resend-verification` returns `200` for unknown email.
- `/health` `200`; `/health/ready` reports checks (can stub deps).

Frontend (vitest, light): `api/client` attaches bearer + retries once on 401; `ProtectedRoute`
redirects when unauthed. Keep minimal.

---

## 13. File-by-file change list

**Backend — implement (currently stubs):**
`core/security.py`, `db/base.py`, `db/session.py`, `models/user.py`, `api/deps.py`,
`api/routes/auth.py`, `config.py` (add §4 settings), `main.py` (add readiness), `workers/celery_app.py` (minimal app).

**Backend — new:**
`models/refresh_token.py`, `models/email_verification_token.py`,
`schemas/auth.py`, `schemas/user.py`, `services/email.py`, `services/email_templates/verify.html`,
`db/migrations/` (Alembic: `alembic.ini`, `env.py`, `script.py.mako`, first revision),
`Dockerfile`, `entrypoint.sh`.

**Frontend — new/implement:**
`index.html`, `src/main.tsx`, `vite.config.ts`, `tsconfig.json`, `tsconfig.node.json`,
`src/auth/AuthContext.tsx`, `src/api/client.ts` (impl), `src/App.tsx` (routing + ProtectedRoute),
`src/pages/Login.tsx`, `src/pages/Register.tsx`, `src/pages/Verify.tsx`,
placeholder `Upload/Review/History.tsx`, `Dockerfile`.

**Infra:**
`docker-compose.yml` (Mailpit, healthchecks, healthy-gated depends_on, migration entrypoint),
`.env.example` (§4 vars).

**Untouched this phase (stay stubs):** everything under `agent/`, `services/ocr/`,
`services/extraction.py`, `services/storage.py`, `services/form_renderer.py`,
`core/encryption.py`, `metrics/`, `templates/`, and the documents/profile/forms/history routers.

---

## 14. Non-goals reminder (don't drift)
No document upload, OCR, profile, form fill, agent, encryption, or metrics work in Phase 0 —
those are Phases 1–6. This phase exists solely to give every later feature a real, authenticated,
verified `User` to hang data off, and a proven `docker compose up` stack.
