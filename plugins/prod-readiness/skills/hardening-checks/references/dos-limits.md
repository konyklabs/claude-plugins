# Denial-of-service surface

The `dos-surface` check, one sub-row per control, for a Python web
backend (FastAPI or Starlette on uvicorn or gunicorn, Flask, Django) with
a reverse proxy and a container in front. Each: what the control prevents,
what "present" looks like, the fix.

## Contents

1. Request body size limit
2. Outbound timeouts
3. Rate limiting
4. Concurrency and keep-alive caps
5. Pagination caps
6. Regex DoS
7. Upload limits
8. Container hardening
9. CORS
10. Debug flags
11. Graceful shutdown

## 1. Request body size limit

One request with a multi-gigabyte body ties a worker up and fills memory.
Present: `client_max_body_size` in nginx, `MAX_CONTENT_LENGTH` in Flask,
`DATA_UPLOAD_MAX_MEMORY_SIZE` in Django, a body-limit middleware in
Starlette. Fix: set it at the proxy *and* in the app; the app limit is
what protects a direct hit.

## 2. Outbound timeouts

An upstream that stops answering holds every worker that called it.
Present: every `httpx` and `requests` call carries `timeout=`, or the
client was built with one (`httpx.Client(timeout=httpx.Timeout(5.0,
connect=2.0))`). The scanner flags each call line without `timeout` when
the file's client construction has none either. Fix: one client per
upstream with a timeout, and no bare module-level `requests.get`.

## 3. Rate limiting

Present: `slowapi` (FastAPI), `flask-limiter`, a proxy rule
(`limit_req`). Keyed by client identity, not just IP, where clients share
addresses. Fix: a limiter on the write endpoints first, reads second.

## 4. Concurrency and keep-alive caps

Present: `uvicorn --limit-concurrency N --timeout-keep-alive 5`,
`gunicorn --workers N --timeout 30 --max-requests M`, or the equivalent in
the container command, a Procfile, or `pyproject` scripts. Fix: cap
concurrency below what the database pool and the upstream allow; keep-alive
short so idle connections do not pin workers.

## 5. Pagination caps

A list endpoint with `limit` and no maximum returns the table. Present:
`limit: int = Query(50, le=200)` or a clamp `min(limit, MAX_PAGE)`. Fix:
the cap in the parameter declaration, so the schema documents it.

## 6. Regex DoS

A pattern with a nested quantifier (`(\w+)+`, `(a|aa)+`, `(.*)*`) on user
input takes exponential time on a crafted string. The scanner flags
`re.compile`/`re.match`/`re.search` patterns whose text has a quantified
group followed by a quantifier. Fix: rewrite the pattern, or bound the
input length before matching.

## 7. Upload limits

Present: a size check before an upload is read into memory
(`UploadFile.size`, `Content-Length`, a spooled temporary file with a
limit). Fix: reject over the limit before reading.

## 8. Container hardening

Per `Dockerfile`: `HEALTHCHECK` present so the orchestrator restarts a
wedged process; `USER` non-root so a code-execution bug is not root;
`FROM image@sha256:...` so the base cannot change under a rebuild. Per
compose file: `deploy.resources.limits` (memory, cpus) so one container
cannot starve the host. Fix: all four; the digest pin is the one people
skip, and it is the supply-chain one.

## 9. CORS

`allow_origins=["*"]` with `allow_credentials=True` lets any site call the
API with the user's cookies. Fix: an explicit origin list when credentials
are allowed.

## 10. Debug flags

`debug=True`, `DEBUG = True`, `reload=True` in non-test code: tracebacks
with locals served to clients, a reloader watching the filesystem in
production. Fix: from the environment, default off, with the test asserting
the default.

## 11. Graceful shutdown

A process killed mid-request loses the request. Present: a lifespan
handler or `SIGTERM` handler that stops accepting and drains. Absent is
`review`, not `fail`: the process manager may drain for you.
