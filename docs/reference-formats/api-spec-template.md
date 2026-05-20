# API Specification
# Example Project — REST API v1

---

## Document Information

| Field | Value |
|-------|-------|
| Document Version | 1.0 |
| Created Date | 2026-05-20 |
| Last Updated | 2026-05-20 |
| Status | Draft |
| API Version | v1 |
| Spec Format | OpenAPI 3.1 |
| Owner | TBD |

---

## Table of Contents

1. [Overview](#1-overview)
2. [Conventions](#2-conventions)
   - 2.1 Base URL + Versioning
   - 2.2 Authentication
   - 2.3 Standard Request Headers
   - 2.4 Standard Response Headers
   - 2.5 Response Envelope
   - 2.6 Error Response Format (RFC 7807)
   - 2.7 Pagination
   - 2.8 Filtering, Sorting, Field Selection
   - 2.9 Rate Limiting
   - 2.10 Idempotency
   - 2.11 Caching (ETag + If-None-Match)
   - 2.12 Webhooks
3. [Resources](#3-resources)
4. [Endpoints](#4-endpoints)
   - 4.1 Users
   - 4.2 Sessions (Auth)
   - 4.3 Projects
   - 4.4 Health & Diagnostics
5. [Data Models](#5-data-models)
6. [Status Code Reference](#6-status-code-reference)
7. [Versioning & Deprecation Policy](#7-versioning--deprecation-policy)
8. [Security Considerations](#8-security-considerations)
9. [OpenAPI Specification](#9-openapi-specification)
10. [Changelog](#10-changelog)
11. [Appendix](#11-appendix)

---

## 1. Overview

The Example Project REST API exposes the application's domain over HTTPS using JSON request and response bodies. The surface area is split into resource-oriented endpoints under `/api/v1/...`, with consistent conventions for authentication, pagination, errors, and rate limiting. This document is the canonical contract — every client (web, mobile, partner integrations, SDKs) MUST follow it; every server change MUST update it in lockstep.

### 1.1 Audience

| Reader | What they get from this doc |
|---|---|
| Frontend / mobile engineers | Endpoint contracts, request/response shapes, auth flow |
| Partner integrators | Stable surface area + deprecation policy + webhook delivery |
| Backend engineers | Source of truth for the canonical OpenAPI spec generated from this doc |
| QA / SRE | Status codes, rate limits, error envelopes — what to assert in tests + monitors |
| Security / compliance | Authentication, authorization, data exposure, PII handling |

### 1.2 Design Principles

- **REST-ish, not religion.** Resources are nouns, HTTP verbs are actions. Pragmatic exceptions are documented inline where they exist.
- **Predictable shapes.** Every response wraps payload in `{data, meta, error}`. Every error uses RFC 7807. Every list endpoint paginates the same way.
- **Versioned at the URL.** Path-prefix `/api/v1/...`. Breaking changes ship as `/v2`; non-breaking additions stay in `/v1`.
- **Idempotent where it matters.** `GET`, `PUT`, `DELETE` are idempotent by HTTP contract. Non-idempotent verbs (`POST`) support an `Idempotency-Key` header.
- **Explicit over implicit.** No magic query params. No silent server-side defaults that change client behavior. Document every header, every status code, every state transition.

---

## 2. Conventions

### 2.1 Base URL + Versioning

| Environment | Base URL |
|---|---|
| Production | `https://api.example.com/v1` |
| Staging | `https://api.staging.example.com/v1` |
| Local | `http://localhost:8000/api/v1` |

The major version (`/v1`) is part of the URL. A new major version (`/v2`) is reserved for **breaking** changes only. Additive changes (new endpoints, new optional fields, new query parameters, new optional headers) ship inside the current major version without breaking existing clients.

### 2.2 Authentication

All endpoints except those marked **PUBLIC** require a Bearer JWT in the `Authorization` header.

```
Authorization: Bearer <jwt>
```

JWTs are issued by `POST /auth/login`, signed with HS256, and expire after 30 minutes by default. Use `POST /auth/refresh` to extend a session via the refresh token returned in an httpOnly cookie. Tokens carry these claims:

| Claim | Required | Description |
|---|---|---|
| `sub` | yes | User ID (`u-<8hex>`) |
| `iat` | yes | Issued-at (epoch seconds) |
| `exp` | yes | Expiry (epoch seconds) |
| `role` | yes | One of `viewer`, `developer`, `admin` |
| `scopes` | optional | Array of fine-grained permissions for partner tokens |
| `tenant_id` | optional | Multi-tenant scoping (future) |

**Failures:**
- Missing header → `401 Unauthorized` with `type=https://errors.example.com/auth/missing-token`
- Invalid signature / expired → `401 Unauthorized` with `type=https://errors.example.com/auth/invalid-token`
- Insufficient role / scope → `403 Forbidden` with `type=https://errors.example.com/auth/forbidden`

### 2.3 Standard Request Headers

| Header | Required | Default | Notes |
|---|---|---|---|
| `Content-Type` | on `POST`/`PATCH`/`PUT` | `application/json; charset=utf-8` | Reject `text/plain` with `415 Unsupported Media Type` |
| `Accept` | optional | `application/json` | We don't negotiate XML / form-encoded responses |
| `Authorization` | required (most endpoints) | — | `Bearer <jwt>`; see §2.2 |
| `X-Request-ID` | optional | server-generated | Client-supplied trace ID echoed back in response headers + logs. UUIDv4. |
| `Idempotency-Key` | optional on POST | — | See §2.10 |
| `If-None-Match` | optional on GET | — | See §2.11 |

### 2.4 Standard Response Headers

| Header | Always present? | Meaning |
|---|---|---|
| `Content-Type` | yes | `application/json; charset=utf-8` for JSON, `application/problem+json` for errors |
| `X-Request-ID` | yes | Echoed from request or server-generated. **Quote this in any support ticket.** |
| `X-RateLimit-Limit` | yes | Requests permitted in the current window (see §2.9) |
| `X-RateLimit-Remaining` | yes | Requests left in this window |
| `X-RateLimit-Reset` | yes | Epoch seconds when the window resets |
| `ETag` | on GET of a single resource | Strong validator — pair with `If-None-Match` for caching |
| `Retry-After` | on `429` / `503` | Seconds to wait before retrying |
| `Deprecation` | on deprecated endpoints | RFC 8594 — date when retirement begins |
| `Sunset` | on deprecated endpoints | RFC 8594 — date when the endpoint is removed |

### 2.5 Response Envelope

Every **non-error** response wraps the payload in a uniform envelope:

```json
{
  "data": <object | array | null>,
  "meta": <object | null>,
  "error": null
}
```

- `data`: the resource(s). Object for single-resource endpoints, array for list endpoints, `null` for endpoints that complete with no payload (e.g. `DELETE`).
- `meta`: optional contextual data — pagination cursors, counts, server timestamp, feature flags. `null` when not relevant.
- `error`: always `null` on success. The presence of a non-null `error` MUST coincide with a 4xx/5xx status.

**Example (single resource):**
```json
{
  "data": {
    "user_id": "u-7d4a9c12",
    "email": "ada@example.com",
    "role": "developer",
    "created_at": "2026-05-19T13:22:01Z"
  },
  "meta": null,
  "error": null
}
```

**Example (list, paginated):**
```json
{
  "data": [
    { "user_id": "u-7d4a9c12", "email": "ada@example.com", "role": "developer" },
    { "user_id": "u-2c1b88f0", "email": "lin@example.com", "role": "viewer" }
  ],
  "meta": {
    "next_cursor": "eyJpZCI6InUtMmMxYjg4ZjAifQ==",
    "prev_cursor": null,
    "page_size": 2,
    "total_estimate": 184
  },
  "error": null
}
```

### 2.6 Error Response Format (RFC 7807)

Errors use **`application/problem+json`** per [RFC 7807](https://www.rfc-editor.org/rfc/rfc7807):

```json
{
  "type": "https://errors.example.com/validation/invalid-field",
  "title": "Invalid field value",
  "status": 422,
  "detail": "Field 'email' must be a valid RFC 5322 email address.",
  "instance": "/v1/users",
  "errors": [
    { "field": "email", "code": "format_invalid", "message": "must be a valid email" }
  ],
  "request_id": "f0c4a4ce-8c1c-4a05-b73c-2dba8a45e3a4"
}
```

Required members:
- `type` — stable URI; the SAME type for the SAME failure across deploys.
- `title` — short human-readable label (NEVER reformat — clients may match on it).
- `status` — repeats the HTTP status code.
- `detail` — human-readable explanation specific to this occurrence.
- `instance` — the URL path of the failing request.
- `request_id` — UUID echoed in `X-Request-ID`. Quote this in support tickets.

Optional members:
- `errors[]` — field-level breakdown for validation errors (422).
- `retry_after_seconds` — programmatic retry hint when `Retry-After` header is also set.

**Error catalog** (registered `type` URIs — clients can switch on these):

| Type URI suffix | HTTP status | When |
|---|---|---|
| `/auth/missing-token` | 401 | No `Authorization` header |
| `/auth/invalid-token` | 401 | Signature / expiry / malformed |
| `/auth/forbidden` | 403 | Authenticated but insufficient role or scope |
| `/validation/invalid-field` | 422 | Body / query field failed validation |
| `/validation/missing-field` | 422 | Required field absent |
| `/conflict/duplicate-resource` | 409 | Unique constraint violation |
| `/conflict/state-transition` | 409 | Resource is not in a state that permits this verb |
| `/not-found/resource` | 404 | Resource ID does not exist or is not visible to caller |
| `/rate-limit/exceeded` | 429 | Quota exhausted; see `Retry-After` |
| `/server/internal` | 500 | Unexpected — file a bug with `request_id` |
| `/server/upstream-timeout` | 504 | Downstream dependency timed out |

### 2.7 Pagination

List endpoints use **opaque cursor-based pagination**. Cursors are URL-safe base64 strings; treat them as black boxes — clients MUST NOT parse them.

| Query parameter | Default | Max | Notes |
|---|---|---|---|
| `cursor` | — | — | Returned in `meta.next_cursor`. Omit for the first page. |
| `page_size` | 50 | 200 | Server caps silently; check `meta.page_size` for the effective value. |

Responses carry:
- `meta.next_cursor` — present when more pages exist; `null` when the caller has reached the end.
- `meta.prev_cursor` — present when not on the first page.
- `meta.page_size` — the effective page size the server used.
- `meta.total_estimate` — best-effort row count; may be approximate for large collections. Use for UI hints, not for exact business logic.

### 2.8 Filtering, Sorting, Field Selection

- **Filter**: query-string equality match on indexed fields, e.g. `?status=active&role=developer`. Range filters use suffix: `?created_at_gte=2026-01-01&created_at_lt=2026-02-01`.
- **Sort**: `?sort=-created_at,name` — leading `-` for descending, comma-separated for tiebreaks. Server rejects unindexed sort keys with `422`.
- **Field selection**: `?fields=user_id,email,role` — server returns only requested keys plus the resource's primary key. Useful for keeping mobile responses small.

### 2.9 Rate Limiting

Per-token sliding window:

| Tier | Requests | Window | Burst |
|---|---|---|---|
| `viewer` | 60 | 60s | 30 |
| `developer` | 600 | 60s | 200 |
| `admin` | 1,200 | 60s | 400 |
| Partner integrations | per contract | — | per contract |

On exceed, server returns `429 Too Many Requests` with:
- `Retry-After: <seconds>` header
- RFC 7807 problem with `type=…/rate-limit/exceeded`
- Body includes `retry_after_seconds` for programmatic backoff

Clients SHOULD honor `Retry-After`; aggressive retry-on-429 will be blocked at the edge.

### 2.10 Idempotency

`POST` endpoints that create resources MAY accept an `Idempotency-Key` header. The server stores `(idempotency_key, route, payload_hash) → response` for 24 hours.

- **Same key + same payload** → returns the cached response (`201 Created` on first call, `200 OK` with `Idempotency-Replayed: true` on subsequent calls).
- **Same key + different payload** → `409 Conflict`, `type=…/conflict/idempotency-mismatch`.
- Clients SHOULD generate one UUIDv4 per logical operation and retry with the same key on network errors.

### 2.11 Caching (ETag + If-None-Match)

`GET` of a single resource returns an `ETag` header (strong validator — content hash). Clients pass it back as `If-None-Match` on subsequent requests; if the resource hasn't changed, the server returns `304 Not Modified` with no body.

List endpoints don't ETag (their content changes too often); they support cursor-pagination only.

### 2.12 Webhooks

The API delivers events to caller-registered HTTPS URLs. See §4 (registration endpoints) for management. Each delivery:

- Signed with HMAC-SHA256 — header `X-Signature: t=<unix_ts>,v1=<hex_digest>`
- Includes `X-Event-Type` and `X-Event-Id`
- Body is a JSON envelope: `{event_type, event_id, occurred_at, data}`
- Retried with exponential backoff up to 24h on non-2xx responses
- Caller MUST verify the signature and treat the endpoint as idempotent (use `event_id`)

---

## 3. Resources

| Resource | Description |
|---|---|
| `User` | A registered account with role + email |
| `Session` | A live JWT-backed authentication session |
| `Project` | A user-owned container for build artifacts (PRD, tasks, deployments) |
| `Webhook` | A caller-registered URL for receiving event deliveries |

---

## 4. Endpoints

### 4.1 Users

#### `GET /users`
> List users. **Admin only.**

| | |
|---|---|
| **Auth** | Bearer (role ≥ `admin`) |
| **Idempotent** | yes |
| **Rate-limited** | yes (tier: caller's role) |
| **Cacheable** | no (list endpoint) |

**Query parameters:**

| Name | Type | Required | Default | Description |
|---|---|---|---|---|
| `cursor` | string | no | — | Pagination cursor from prior response |
| `page_size` | integer | no | 50 | Max 200 |
| `role` | string | no | — | Filter by role: `viewer`, `developer`, `admin` |
| `email_contains` | string | no | — | Case-insensitive substring match |
| `sort` | string | no | `-created_at` | Sort key (see §2.8) |

**Response — `200 OK`:**
```json
{
  "data": [
    { "user_id": "u-7d4a9c12", "email": "ada@example.com", "role": "developer", "created_at": "2026-05-19T13:22:01Z" }
  ],
  "meta": { "next_cursor": "eyJpZCI6InUtMmMxYjg4ZjAifQ==", "page_size": 50, "total_estimate": 184 },
  "error": null
}
```

**Errors:**

| Status | Type | Trigger |
|---|---|---|
| 401 | `/auth/missing-token` or `/auth/invalid-token` | No / bad token |
| 403 | `/auth/forbidden` | Caller's role is not `admin` |
| 422 | `/validation/invalid-field` | `page_size > 200`, unknown sort key, etc. |

**Example:**
```bash
curl -H "Authorization: Bearer $TOKEN" \
     "https://api.example.com/v1/users?role=developer&page_size=20&sort=-created_at"
```

---

#### `POST /users`
> Create a user. **Admin only.**

| | |
|---|---|
| **Auth** | Bearer (role ≥ `admin`) |
| **Idempotent** | no (creates a new row) — supports `Idempotency-Key` |
| **Rate-limited** | yes |

**Request body:**
```json
{
  "email": "ada@example.com",
  "role": "developer",
  "password": "<at-least-12-chars>"
}
```

| Field | Type | Required | Constraints |
|---|---|---|---|
| `email` | string | yes | RFC 5322; lowercased server-side |
| `role` | string | yes | One of `viewer`, `developer`, `admin` |
| `password` | string | yes | ≥12 chars, ≥1 letter + 1 digit |

**Response — `201 Created`:**

`Location: /v1/users/u-7d4a9c12`

```json
{
  "data": {
    "user_id": "u-7d4a9c12",
    "email": "ada@example.com",
    "role": "developer",
    "created_at": "2026-05-20T10:00:00Z"
  },
  "meta": null,
  "error": null
}
```

**Errors:**

| Status | Type | Trigger |
|---|---|---|
| 401 / 403 | auth errors | as above |
| 409 | `/conflict/duplicate-resource` | Email already in use |
| 422 | `/validation/invalid-field` | Weak password / bad role / malformed email |

---

#### `GET /users/{user_id}`
> Read a single user. **Admin or self.**

| | |
|---|---|
| **Auth** | Bearer (role `admin` OR `sub == user_id`) |
| **Cacheable** | yes — returns `ETag` |

**Path parameters:**

| Name | Type | Description |
|---|---|---|
| `user_id` | string | Pattern: `u-[0-9a-f]{8}` |

**Response — `200 OK`:** as in `POST /users` above. Includes `ETag` header.

**Response — `304 Not Modified`:** when `If-None-Match` matches current ETag.

**Errors:**

| Status | Type | Trigger |
|---|---|---|
| 404 | `/not-found/resource` | No such user OR caller can't see this user |
| 403 | `/auth/forbidden` | Non-admin reading someone else's record |

---

#### `PATCH /users/{user_id}`
> Update a user's mutable fields. **Admin or self (limited to `email`, `password`).**

**Request body** (partial — only include fields you're changing):
```json
{ "role": "admin", "email": "ada@example.com" }
```

**Response — `200 OK`:** updated resource in standard envelope. New `ETag` header.

**Errors:** 401, 403, 404, 409 (email taken), 422 (validation).

---

#### `DELETE /users/{user_id}`
> Hard-delete. **Admin only.** Cascades to user-owned resources (projects, sessions, webhooks).

**Response — `204 No Content`** — no body.

**Errors:** 401, 403, 404. `409 /conflict/state-transition` if the user owns active billing subscriptions (out-of-scope reference).

---

### 4.2 Sessions (Auth)

#### `POST /auth/login` &nbsp; **PUBLIC**
> Exchange credentials for a JWT.

**Request body:**
```json
{ "email": "ada@example.com", "password": "secret" }
```

**Response — `200 OK`:**
```json
{
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIs…",
    "token_type": "Bearer",
    "expires_in": 1800
  },
  "meta": null,
  "error": null
}
```

The refresh token is delivered as an httpOnly, Secure, SameSite=Lax cookie named `__Host-refresh`. It is never returned in the response body.

**Errors:**

| Status | Type | Trigger |
|---|---|---|
| 401 | `/auth/invalid-credentials` | Wrong email/password |
| 423 | `/auth/account-locked` | Too many failed attempts; see `Retry-After` |
| 422 | `/validation/missing-field` | Email or password absent |

---

#### `POST /auth/refresh` &nbsp; **PUBLIC (cookie-gated)**
> Mint a new access token from the refresh cookie. Refresh token is rotated.

**Response — `200 OK`:** same shape as `/auth/login`.

**Errors:** 401 with `/auth/invalid-token` if cookie missing / expired / revoked.

---

#### `POST /auth/logout`
> Revoke the active session.

**Response — `204 No Content`.** Server clears the refresh cookie and revokes the refresh family.

---

### 4.3 Projects

[Repeat the GET / POST / GET-by-id / PATCH / DELETE pattern from §4.1, scoped to the caller's `sub` unless they're admin. Lists support `?status=active|archived`, `?sort=-updated_at`. Show one example fully; reference §4.1 for repeated patterns.]

---

### 4.4 Health & Diagnostics

#### `GET /health` &nbsp; **PUBLIC**
> Liveness probe. Returns 200 if the process is up.

**Response — `200 OK`:**
```json
{ "data": { "status": "healthy", "version": "1.2.3" }, "meta": null, "error": null }
```

#### `GET /ready` &nbsp; **PUBLIC**
> Readiness probe. Returns 200 if the process can serve traffic (DB reachable, dependencies up).

**Response — `200 OK`** as above, OR **`503 Service Unavailable`** with `data.status = "degraded"` and `data.checks[]` listing which downstream failed.

---

## 5. Data Models

### `User`

| Field | Type | Nullable | Description |
|---|---|---|---|
| `user_id` | string | no | Pattern: `u-[0-9a-f]{8}` |
| `email` | string | no | RFC 5322; lowercased |
| `role` | string | no | `viewer` \| `developer` \| `admin` |
| `created_at` | string (ISO 8601) | no | UTC |
| `updated_at` | string (ISO 8601) | yes | UTC; `null` if never updated |

### `Project`

| Field | Type | Nullable | Description |
|---|---|---|---|
| `project_id` | string | no | Pattern: `proj-[0-9a-f]{8}` |
| `name` | string | no | 1-80 chars; filesystem-safe |
| `description` | string | no | ≤500 chars |
| `status` | string | no | `active` \| `archived` |
| `lead_user_id` | string | yes | FK to `User.user_id` |
| `repo_url` | string | no | Default `""`; HTTPS URL when set |
| `created_at` | string (ISO 8601) | no | UTC |
| `updated_at` | string (ISO 8601) | yes | UTC |

### `Webhook`

| Field | Type | Nullable | Description |
|---|---|---|---|
| `webhook_id` | string | no | `wh-[0-9a-f]{8}` |
| `target_url` | string | no | HTTPS; validated reachable on create |
| `event_types` | array<string> | no | Whitelist of event types this webhook receives |
| `secret_last4` | string | no | Last 4 chars of the signing secret (rest never echoed) |
| `created_at` | string (ISO 8601) | no | UTC |

---

## 6. Status Code Reference

| Code | Meaning | When the API returns it |
|---|---|---|
| 200 | OK | Successful GET / PATCH |
| 201 | Created | Successful POST that created a resource (sets `Location`) |
| 204 | No Content | Successful DELETE / logout — empty body |
| 304 | Not Modified | `If-None-Match` matched current `ETag` |
| 400 | Bad Request | Malformed JSON, unknown query param, content-type mismatch |
| 401 | Unauthorized | Missing / invalid / expired token |
| 403 | Forbidden | Authenticated but lacks role / scope |
| 404 | Not Found | Resource ID unknown OR invisible to caller |
| 405 | Method Not Allowed | Verb not supported for this path |
| 409 | Conflict | Duplicate, state-transition error, idempotency-mismatch |
| 415 | Unsupported Media Type | `Content-Type` not `application/json` on body endpoints |
| 422 | Unprocessable Entity | Body / query validation failed |
| 423 | Locked | Account locked after too many failed logins |
| 429 | Too Many Requests | Rate limit exceeded; see `Retry-After` |
| 500 | Internal Server Error | Unhandled exception — `request_id` lands in our error tracker |
| 503 | Service Unavailable | Readiness check failed or planned maintenance |
| 504 | Gateway Timeout | Upstream dependency timed out |

---

## 7. Versioning & Deprecation Policy

- **Major versions** (`/v1`, `/v2`) carry **breaking** changes only. We maintain the previous major version for **18 months** after a new one ships.
- **Additive changes** (new endpoint, new field, new optional parameter) ship without a version bump.
- **Deprecation:** announced 90 days before retirement.
  - `Deprecation: <date>` and `Sunset: <date>` headers (RFC 8594) added to responses
  - Deprecation guide published in the Changelog
  - Email + dashboard notice to affected API keys
- **Removal:** on the `Sunset` date, the endpoint returns `410 Gone` with `type=…/deprecation/sunset` and a link to the replacement.

---

## 8. Security Considerations

- **TLS 1.2+** on all endpoints. HTTP requests redirect to HTTPS with `HSTS max-age=31536000; includeSubDomains; preload`.
- **No PII in logs.** Request logging captures method + path + status + `X-Request-ID` + `user_id`. Bodies are never logged. Passwords are never logged.
- **Secrets handling:** signing secrets (webhook HMAC, JWT signing key) are stored in the OS keychain / managed-secret service; never echoed to clients.
- **CORS:** allowlist of approved origins via `Access-Control-Allow-Origin` (no wildcards). Credentials mode enabled only for the auth origin.
- **CSP for API docs surface:** strict (`default-src 'self'`).
- **OWASP API Top 10 alignment:** broken object-level auth, broken auth, broken object property-level auth, unrestricted resource consumption, broken function-level auth, server-side request forgery — checked in CI via Spectral rules + integration tests.

---

## 9. OpenAPI Specification

The OpenAPI 3.1 contract below is the **machine-readable source of truth**. Code generators (TypeScript client, Python client, Postman collection) consume this. Keep this section in sync with §4 — if they disagree, this YAML wins.

```yaml
openapi: 3.1.0
info:
  title: Example Project API
  version: "1.0.0"
  description: |
    REST API for the Example Project platform. See the companion
    api-spec.md document for narrative + examples.
  contact:
    name: API Team
    email: api@example.com
servers:
  - url: https://api.example.com/v1
    description: Production
  - url: https://api.staging.example.com/v1
    description: Staging
  - url: http://localhost:8000/api/v1
    description: Local

security:
  - bearerAuth: []

components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

  parameters:
    Cursor:
      name: cursor
      in: query
      required: false
      schema: { type: string }
    PageSize:
      name: page_size
      in: query
      required: false
      schema: { type: integer, minimum: 1, maximum: 200, default: 50 }

  headers:
    X-Request-ID:
      schema: { type: string, format: uuid }
    X-RateLimit-Limit:
      schema: { type: integer }
    X-RateLimit-Remaining:
      schema: { type: integer }
    X-RateLimit-Reset:
      schema: { type: integer, description: "Epoch seconds" }

  schemas:
    Envelope:
      type: object
      required: [data, meta, error]
      properties:
        data: {}
        meta: { type: object, nullable: true }
        error: { type: object, nullable: true }

    Problem:
      type: object
      required: [type, title, status, detail, instance]
      properties:
        type: { type: string, format: uri }
        title: { type: string }
        status: { type: integer }
        detail: { type: string }
        instance: { type: string }
        request_id: { type: string, format: uuid }
        errors:
          type: array
          items:
            type: object
            properties:
              field: { type: string }
              code: { type: string }
              message: { type: string }

    User:
      type: object
      required: [user_id, email, role, created_at]
      properties:
        user_id: { type: string, pattern: "^u-[0-9a-f]{8}$" }
        email: { type: string, format: email }
        role: { type: string, enum: [viewer, developer, admin] }
        created_at: { type: string, format: date-time }
        updated_at: { type: string, format: date-time, nullable: true }

    CreateUserRequest:
      type: object
      required: [email, role, password]
      properties:
        email: { type: string, format: email }
        role: { type: string, enum: [viewer, developer, admin] }
        password: { type: string, minLength: 12 }

  responses:
    Unauthorized:
      description: Missing or invalid credentials
      content:
        application/problem+json:
          schema: { $ref: "#/components/schemas/Problem" }
    Forbidden:
      description: Authenticated but lacks role / scope
      content:
        application/problem+json:
          schema: { $ref: "#/components/schemas/Problem" }
    NotFound:
      description: Resource not visible to caller
      content:
        application/problem+json:
          schema: { $ref: "#/components/schemas/Problem" }
    Conflict:
      description: Duplicate or state-transition error
      content:
        application/problem+json:
          schema: { $ref: "#/components/schemas/Problem" }
    UnprocessableEntity:
      description: Validation failed
      content:
        application/problem+json:
          schema: { $ref: "#/components/schemas/Problem" }
    TooManyRequests:
      description: Rate limit exceeded
      headers:
        Retry-After: { schema: { type: integer } }
      content:
        application/problem+json:
          schema: { $ref: "#/components/schemas/Problem" }

paths:
  /users:
    get:
      summary: List users (admin only)
      operationId: listUsers
      tags: [Users]
      security: [{ bearerAuth: [] }]
      parameters:
        - $ref: "#/components/parameters/Cursor"
        - $ref: "#/components/parameters/PageSize"
        - name: role
          in: query
          required: false
          schema: { type: string, enum: [viewer, developer, admin] }
      responses:
        "200":
          description: OK
          headers:
            X-Request-ID: { $ref: "#/components/headers/X-Request-ID" }
            X-RateLimit-Limit: { $ref: "#/components/headers/X-RateLimit-Limit" }
            X-RateLimit-Remaining: { $ref: "#/components/headers/X-RateLimit-Remaining" }
            X-RateLimit-Reset: { $ref: "#/components/headers/X-RateLimit-Reset" }
          content:
            application/json:
              schema:
                allOf:
                  - $ref: "#/components/schemas/Envelope"
                  - type: object
                    properties:
                      data:
                        type: array
                        items: { $ref: "#/components/schemas/User" }
        "401": { $ref: "#/components/responses/Unauthorized" }
        "403": { $ref: "#/components/responses/Forbidden" }
        "422": { $ref: "#/components/responses/UnprocessableEntity" }
        "429": { $ref: "#/components/responses/TooManyRequests" }

    post:
      summary: Create a user (admin only)
      operationId: createUser
      tags: [Users]
      security: [{ bearerAuth: [] }]
      parameters:
        - name: Idempotency-Key
          in: header
          required: false
          schema: { type: string, format: uuid }
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/CreateUserRequest" }
      responses:
        "201":
          description: Created
          headers:
            Location: { schema: { type: string } }
          content:
            application/json:
              schema:
                allOf:
                  - $ref: "#/components/schemas/Envelope"
                  - type: object
                    properties:
                      data: { $ref: "#/components/schemas/User" }
        "401": { $ref: "#/components/responses/Unauthorized" }
        "403": { $ref: "#/components/responses/Forbidden" }
        "409": { $ref: "#/components/responses/Conflict" }
        "422": { $ref: "#/components/responses/UnprocessableEntity" }

  # … additional paths follow the same pattern …
```

---

## 10. Changelog

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-20 | Backend Team | Initial public surface |

---

## 11. Appendix

### 11.1 Open Questions

1. Should the `meta.total_estimate` field be removed for very large collections (>10M rows) to avoid expensive counts? — owner: backend
2. Do we need per-tenant rate limits in addition to per-token? — owner: SRE
3. Webhook delivery retries — exponential backoff cap is 24h. Confirm with partner integrations team.

### 11.2 Glossary

| Term | Definition |
|---|---|
| Bearer JWT | Stateless authentication token carried in the `Authorization` header |
| Cursor pagination | Opaque, server-controlled pagination token (vs. offset/page numbers) |
| RFC 7807 | "Problem Details for HTTP APIs" — the error response standard |
| Idempotency | Property where the same request can be replayed without additional side effects |
| ETag | HTTP entity tag — strong content validator for caching |
| Sunset header | RFC 8594 deprecation signaling header |

### 11.3 Reference Implementations

- **TypeScript client** — `@example/api-client` on npm; generated from §9
- **Python client** — `example-api` on PyPI; generated from §9
- **Postman collection** — published at `https://api.example.com/docs/postman.json`

---

*End of Document*
