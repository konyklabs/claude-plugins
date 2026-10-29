#!/usr/bin/env bash
# Scaffold for adopt-clover-compose: a small, generic service that connects a
# merchant's Clover account, with an e2e tier that runs under docker compose
# and no vendor fake yet. Writes files into the current directory only: no git,
# no network, nothing executed.
set -euo pipefail

mkdir -p app tests/e2e

cat > compose.yaml <<'EOF'
services:
  app:
    build: .
    ports: ["127.0.0.1:8000:8000"]
    environment:
      CLOVER_OAUTH_BASE_URL: ""
      CLOVER_API_BASE_URL: ""
      CLOVER_APP_ID: ""
      CLOVER_APP_SECRET: ""
      CLOVER_WEBHOOK_AUTH_CODE: ""
EOF

cat > Dockerfile <<'EOF'
FROM python:3.13-slim
COPY app /app
CMD ["python", "-m", "app"]
EOF

: > app/__init__.py

cat > app/settings.py <<'EOF'
"""The service's Clover settings, read from the environment."""
import os

CLOVER_OAUTH_BASE_URL = os.environ.get("CLOVER_OAUTH_BASE_URL", "")
CLOVER_API_BASE_URL = os.environ.get("CLOVER_API_BASE_URL", "")
CLOVER_APP_ID = os.environ.get("CLOVER_APP_ID", "")
CLOVER_APP_SECRET = os.environ.get("CLOVER_APP_SECRET", "")
CLOVER_WEBHOOK_AUTH_CODE = os.environ.get("CLOVER_WEBHOOK_AUTH_CODE", "")
CLOVER_REDIRECT_URI = os.environ.get("CLOVER_REDIRECT_URI", "http://app:8000/oauth/clover/callback")
EOF

cat > app/clover_client.py <<'EOF'
"""Builds the Clover requests the service makes; the transport lives elsewhere."""
from app import settings


def authorize_url(state):
    """The URL the merchant is sent to: GET /oauth/v2/authorize."""
    return (
        settings.CLOVER_OAUTH_BASE_URL
        + "/oauth/v2/authorize?client_id=" + settings.CLOVER_APP_ID
        + "&redirect_uri=" + settings.CLOVER_REDIRECT_URI
        + "&state=" + state
    )


def exchange_code(code):
    """POST /oauth/v2/token: the URL and the JSON body for the code exchange."""
    url = settings.CLOVER_OAUTH_BASE_URL + "/oauth/v2/token"
    body = {"client_id": settings.CLOVER_APP_ID, "client_secret": settings.CLOVER_APP_SECRET, "code": code}
    return url, body


def merchant(merchant_id, token):
    """GET /v3/merchants/{merchant_id}: the URL and the headers."""
    url = settings.CLOVER_API_BASE_URL + "/v3/merchants/" + merchant_id
    return url, {"Authorization": "Bearer " + token}
EOF

: > tests/e2e/.gitkeep

cat > README.md <<'EOF'
A service that connects merchants' Clover accounts.
The e2e tier runs under docker compose: bring the stack up, then run the e2e tier.
EOF
