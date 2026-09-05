"""Credentials coverage for the fixture app.

Only name validation is claimed here; creating a credential, showing its
secret once and revoking one are deliberately left uncovered. See
../README.md for the full list and why.
"""

import pytest
from playwright.sync_api import expect

from conftest import login


@pytest.mark.tile("credentials.create.name-validation")
def test_invalid_credential_name_shows_error(page, base_url):
    login(page, "member")
    page.goto("/credentials/new")
    page.get_by_label("Name").fill("a")
    page.get_by_role("button", name="Create").click()
    expect(page.locator('[data-state="error:invalid-name"]')).to_be_visible()
