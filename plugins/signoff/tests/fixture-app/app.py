"""Fixture developer-portal application for the signoff plugin's suite.

FastAPI, server-rendered HTML from f-strings, in-memory state, one file.
Every account and secret below is an obvious placeholder; see README.md for
the full account list and what the suite deliberately leaves uncovered.
"""

import re
import secrets
from typing import Optional
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # /docs is this app's own screen

USERS = {
    "member@example.invalid": {
        "password": "placeholder-member",
        "role": "member",
        "name": "Mel Member",
    },
    "admin@example.invalid": {
        "password": "placeholder-admin",
        "role": "admin",
        "name": "Ada Admin",
    },
}

SESSIONS = {}  # token -> email
CREDENTIALS = []  # [{"id", "name", "owner", "secret"}]
_next_credential_id = 1

NAME_RE = re.compile(r"^[A-Za-z0-9-]{3,40}$")


async def read_form(request: Request) -> dict:
    """Parse an application/x-www-form-urlencoded body with the stdlib only."""
    body = await request.body()
    parsed = parse_qs(body.decode())
    return {key: values[0] for key, values in parsed.items()}


def layout(title: str, body: str, user: Optional[dict] = None) -> str:
    links = ['<a href="/docs">Docs</a>']
    if user:
        links.append('<a href="/">Home</a>')
        links.append('<a href="/members">Members</a>')
        links.append('<a href="/credentials">Credentials</a>')
        if user["role"] == "admin":
            links.append('<a href="/admin">Admin</a>')
        links.append(
            '<form method="post" action="/logout" style="display:inline">'
            '<button type="submit">Sign out</button></form>'
        )
    else:
        links.append('<a href="/login">Sign in</a>')
    nav = " | ".join(links)
    return (
        f"<!doctype html><html><head><title>{title}</title></head>"
        f"<body><nav>{nav}</nav><main>{body}</main></body></html>"
    )


def current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get("session")
    email = SESSIONS.get(token) if token else None
    if email is None or email not in USERS:
        return None
    user = dict(USERS[email])
    user["email"] = email
    return user


class Redirect(Exception):
    """Raised by a guard to send the browser to another screen."""

    def __init__(self, location: str):
        self.location = location


@app.exception_handler(Redirect)
async def handle_redirect(request: Request, exc: Redirect) -> RedirectResponse:
    return RedirectResponse(exc.location, status_code=303)


def require_login(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        raise Redirect("/login")  # auth.session.required-for-app
    return user


# -- auth --------------------------------------------------------------------

LOGIN_FORM = """
<h1>Sign in</h1>
{error}
<form method="post" action="/login">
  <label>Email <input name="email" type="email"></label>
  <label>Password <input name="password" type="password"></label>
  <button type="submit">Sign in</button>
</form>
"""


@app.get("/login", response_class=HTMLResponse)
async def login_form() -> HTMLResponse:
    return HTMLResponse(layout("Sign in", LOGIN_FORM.format(error="")))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(form: dict = Depends(read_form)) -> HTMLResponse:
    email = form.get("email", "")
    password = form.get("password", "")
    user = USERS.get(email)
    if user is None or user["password"] != password:
        error = (
            '<p class="error" data-state="error:invalid-credentials">'
            "Invalid email or password.</p>"
        )
        return HTMLResponse(  # auth.sign-in.wrong-password
            layout("Sign in", LOGIN_FORM.format(error=error)), status_code=401
        )
    token = secrets.token_hex(16)
    SESSIONS[token] = email
    response = RedirectResponse("/", status_code=303)  # auth.sign-in.valid-password
    response.set_cookie("session", token, httponly=True)
    return response


@app.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get("session")
    SESSIONS.pop(token, None)  # auth.sign-out.clears-session
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session")
    return response


# -- org -----------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def org_home(user: dict = Depends(require_login)) -> HTMLResponse:
    body = f"<h1>Welcome, {user['name']}</h1><p>Role: {user['role']}</p>"
    return HTMLResponse(layout("Home", body, user))


@app.get("/members", response_class=HTMLResponse)
async def members_list(user: dict = Depends(require_login)) -> HTMLResponse:
    rows = []
    for email, info in USERS.items():
        remove_button = ""
        if user["role"] == "admin" and email != user["email"]:
            remove_button = (
                f'<form method="post" action="/members/{email}/remove" '
                f'style="display:inline"><button type="submit">Remove</button></form>'
            )
        rows.append(f"<li>{info['name']} ({info['role']}) {remove_button}</li>")
    body = f"<h1>Members</h1><ul>{''.join(rows)}</ul>"
    return HTMLResponse(layout("Members", body, user))


@app.post("/members/{email}/remove", response_class=HTMLResponse)
async def members_remove(email: str, user: dict = Depends(require_login)) -> HTMLResponse:
    if user["role"] != "admin":
        body = (  # org.members.remove.requires-admin
            '<h1>Forbidden</h1><p data-state="error:forbidden">Admins only.</p>'
        )
        return HTMLResponse(layout("Forbidden", body, user), status_code=403)
    USERS.pop(email, None)
    return RedirectResponse("/members", status_code=303)


# -- credentials -----------------------------------------------------------------

@app.get("/credentials", response_class=HTMLResponse)
async def credentials_list(user: dict = Depends(require_login)) -> HTMLResponse:
    mine = [c for c in CREDENTIALS if c["owner"] == user["email"]]
    rows = []
    for cred in mine:
        can_revoke = cred["owner"] == user["email"] or user["role"] == "admin"
        revoke = ""
        if can_revoke:
            revoke = (
                f'<form method="post" action="/credentials/{cred["id"]}/revoke" '
                f'style="display:inline"><button type="submit">Revoke</button></form>'
            )
        rows.append(f"<li>{cred['name']} {revoke}</li>")
    body = (
        f"<h1>Credentials</h1><ul>{''.join(rows)}</ul>"
        '<a href="/credentials/new">New credential</a>'
    )
    return HTMLResponse(layout("Credentials", body, user))


NEW_CREDENTIAL_FORM = """
<h1>New credential</h1>
{error}
<form method="post" action="/credentials/new">
  <label>Name <input name="name"></label>
  <button type="submit">Create</button>
</form>
"""


@app.get("/credentials/new", response_class=HTMLResponse)
async def credentials_new_form(user: dict = Depends(require_login)) -> HTMLResponse:
    return HTMLResponse(
        layout("New credential", NEW_CREDENTIAL_FORM.format(error=""), user)
    )


@app.post("/credentials/new", response_class=HTMLResponse)
async def credentials_new_submit(
    form: dict = Depends(read_form), user: dict = Depends(require_login)
) -> HTMLResponse:
    global _next_credential_id
    name = form.get("name", "")
    if not NAME_RE.match(name):
        error = (
            '<p class="error" data-state="error:invalid-name">'
            "Use 3-40 letters, digits or dashes.</p>"
        )
        return HTMLResponse(  # credentials.create.name-validation
            layout("New credential", NEW_CREDENTIAL_FORM.format(error=error), user),
            status_code=400,
        )
    secret = "placeholder-" + secrets.token_hex(8)
    CREDENTIALS.append(
        {"id": _next_credential_id, "name": name, "owner": user["email"], "secret": secret}
    )
    _next_credential_id += 1
    body = (  # credentials.create.shows-secret-once
        f"<h1>Credential created</h1><p>Secret (shown once): <code>{secret}</code></p>"
        '<p><a href="/credentials">Back to credentials</a></p>'
    )
    return HTMLResponse(layout("Credential created", body, user))


@app.post("/credentials/{credential_id}/revoke", response_class=HTMLResponse)
async def credentials_revoke(
    credential_id: int, user: dict = Depends(require_login)
) -> HTMLResponse:
    credential = next((c for c in CREDENTIALS if c["id"] == credential_id), None)
    if credential is None:
        return RedirectResponse("/credentials", status_code=303)
    if credential["owner"] != user["email"] and user["role"] != "admin":
        body = (  # credentials.revoke.requires-owner-or-admin
            '<h1>Forbidden</h1><p data-state="error:forbidden">'
            "Only the owner or an admin can revoke this.</p>"
        )
        return HTMLResponse(layout("Forbidden", body, user), status_code=403)
    CREDENTIALS.remove(credential)
    return RedirectResponse("/credentials", status_code=303)


# -- docs and admin ----------------------------------------------------------------

@app.get("/docs", response_class=HTMLResponse)
async def docs_home(request: Request) -> HTMLResponse:
    user = current_user(request)  # docs.home.public: open to anonymous, no require_login
    body = "<h1>Docs</h1><p>Public developer documentation.</p>"
    return HTMLResponse(layout("Docs", body, user))


@app.get("/admin", response_class=HTMLResponse)
async def admin_settings(user: dict = Depends(require_login)) -> HTMLResponse:
    if user["role"] != "admin":
        body = (  # admin.settings.requires-admin
            '<h1>Forbidden</h1><p data-state="error:forbidden">Admins only.</p>'
        )
        return HTMLResponse(layout("Forbidden", body, user), status_code=403)
    body = "<h1>Admin settings</h1><p>Placeholder settings page.</p>"
    return HTMLResponse(layout("Admin", body, user))
