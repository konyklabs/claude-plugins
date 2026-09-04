"""Auth, session and public-docs coverage for the fixture app.

Deliberately uncovered: auth.sign-out.clears-session, org.members.render.*,
org.members.remove.requires-admin, admin.settings.requires-admin,
credentials.revoke.requires-owner-or-admin, credentials.create.shows-secret-once,
and every render tile but auth.login.render.anonymous and org.home.render.member.
See ../README.md for the full list.
"""

import pytest
from playwright.sync_api import expect

from conftest import login


@pytest.mark.tile("auth.login.render.anonymous")
def test_login_page_renders_for_anonymous(page, base_url):
    page.goto("/login")
    expect(page.get_by_label("Email")).to_be_visible()
    expect(page.get_by_label("Password")).to_be_visible()
    expect(page.get_by_role("button", name="Sign in")).to_be_visible()


@pytest.mark.tile("auth.sign-in.valid-password")
@pytest.mark.tile("org.home.render.member")
def test_sign_in_with_valid_password(page, base_url):
    login(page, "member")
    expect(page).to_have_url(base_url + "/")
    expect(page.get_by_role("heading", name="Welcome, Mel Member")).to_be_visible()


@pytest.mark.tile("auth.sign-in.wrong-password")
def test_sign_in_with_wrong_password(page, base_url):
    page.goto("/login")
    page.get_by_label("Email").fill("member@example.invalid")
    page.get_by_label("Password").fill("not-the-password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.locator('[data-state="error:invalid-credentials"]')).to_be_visible()


@pytest.mark.tile("auth.session.required-for-app")
def test_unauthenticated_access_redirects_to_login(page, base_url):
    page.goto("/")
    expect(page).to_have_url(base_url + "/login")


@pytest.mark.tile("docs.home.public")
def test_docs_are_public_for_anonymous(page, base_url):
    page.goto("/docs")
    expect(page).to_have_url(base_url + "/docs")
    expect(page.get_by_role("heading", name="Docs")).to_be_visible()
