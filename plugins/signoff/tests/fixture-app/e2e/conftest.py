"""Starts the fixture app for the suite and offers a small login helper.

The app module lives one directory up from this file (fixture-app/app.py),
which is not on sys.path by pytest's default rootdir rules for a test
directory without an __init__.py, so it is added explicitly below.
"""

import os
import socket
import sys
import threading
import time

import pytest
import uvicorn

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as fastapi_app  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def base_url():
    """Runs the fixture app with uvicorn in a background thread for the session."""
    port = _free_port()
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("fixture app did not start within 10s")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


ACCOUNTS = {
    "member": ("member@example.invalid", "placeholder-member"),
    "admin": ("admin@example.invalid", "placeholder-admin"),
}


def login(page, role: str) -> None:
    """Signs the page in as the named role ("member" or "admin") via the real form."""
    email, password = ACCOUNTS[role]
    page.goto("/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
