"""Auth, session and public-docs coverage for the fixture app.

One test here is disabled on purpose (see test_sign_out_clears_the_session):
a skipped test still carries its tile claim, and the fixture exists so that
tile.py can be shown keeping such a claim out of `tests` and the tile in the
gap list.

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


# Disabled, and claiming a tile anyway: exactly the shape that would otherwise
# report `auth.sign-out.clears-session` as covered by a test that never runs.
# tile.py records the claim under the tile's `skipped_tests`, leaves the tile
# uncovered and keeps it in the ranked gaps (expected.json's `skipped`).
@pytest.mark.skip(reason="placeholder: the sign-out flow is not stable yet")
@pytest.mark.tile("auth.sign-out.clears-session")
def test_sign_out_clears_the_session(page, base_url):
    login(page, "member")
    page.get_by_role("button", name="Sign out").click()
    expect(page).to_have_url(base_url + "/login")
    page.goto("/")
    expect(page).to_have_url(base_url + "/login")


# Claims the render tile, not the `docs.home.public` rule: the rule is the
# fixture's one low-risk rule and stays uncovered on purpose, so the gap
# ranking has a low rule to place between the medium error tiles and the low
# render tiles (the review of roadmap#120 found the order untestable without it).
@pytest.mark.tile("docs.home.render.anonymous")
def test_docs_are_public_for_anonymous(page, base_url):
    page.goto("/docs")
    expect(page).to_have_url(base_url + "/docs")
    expect(page.get_by_role("heading", name="Docs")).to_be_visible()
