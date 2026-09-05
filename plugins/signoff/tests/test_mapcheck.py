"""Unit tests for signoff's mapcheck.py.

Every map here is written inline: the point of each test is one thing wrong
with an otherwise valid map, so the two have to be readable side by side.
"""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "skills"
MAPCHECK_SCRIPT = SKILLS / "exploring-app" / "scripts" / "mapcheck.py"


def _load(name, path):
    """Import a script by path: the scripts are run by path, not installed."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mapcheck = _load("signoff_mapcheck", MAPCHECK_SCRIPT)

VALID = {
    "base_url": "http://localhost:8000",
    "explored_at": "2026-09-04T18:00:00Z",
    "roles": ["anonymous", "member", "admin"],
    "screens": [
        {"id": "auth.login", "area": "auth", "path": "/login", "title": "Sign in",
         "roles": ["anonymous"],
         "actions": [{"id": "submit", "kind": "submit", "label": "Sign in", "to": "org.home"},
                     {"id": "docs", "kind": "navigate", "label": "Docs", "to": "docs.home"}],
         "forms": [{"id": "login", "fields": ["email", "password"]}],
         "states": ["error:invalid-credentials"],
         "links": ["docs.home"]},
        {"id": "org.home", "area": "org", "path": "/", "title": "Home",
         "roles": ["member", "admin"],
         "actions": [{"id": "sign-out", "kind": "submit", "label": "Sign out", "to": "auth.login"},
                     {"id": "theme", "kind": "toggle", "label": "Dark"},
                     {"id": "remove", "kind": "mutate", "label": "Remove"}],
         "states": ["error:forbidden"], "links": []},
        {"id": "docs.home", "area": "docs", "path": "/docs", "title": "Docs",
         "roles": ["anonymous", "member", "admin"], "actions": [], "states": [], "links": []},
    ],
    "flows": [
        {"id": "auth.sign-in", "area": "auth", "name": "Sign in", "role": "anonymous",
         "steps": ["auth.login", "org.home"]},
        {"id": "docs.read", "area": "docs", "name": "Read the docs", "role": "anonymous",
         "steps": ["docs.home"]},
    ],
}


def write_map(tmp_path, document):
    path = tmp_path / "map.json"
    with open(str(path), "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    return str(path)


def run(tmp_path, document):
    return mapcheck.main([write_map(tmp_path, document)])


def broken(**changes):
    """A copy of the valid map with one thing wrong."""
    document = copy.deepcopy(VALID)
    for key, value in changes.items():
        document[key] = value
    return document


def test_a_valid_map_passes_and_prints_its_counts(tmp_path, capsys):
    assert run(tmp_path, VALID) == 0
    out = capsys.readouterr().out
    assert "screens: 3" in out
    assert "flows: 2" in out
    assert "roles: 3" in out
    assert "actions: 5 (navigate 1, submit 2, toggle 1, mutate 1)" in out
    assert "error states: 2" in out
    assert "map ok" in out


def test_every_broken_screen_reference_is_named(tmp_path, capsys):
    screens = copy.deepcopy(VALID["screens"])
    screens[0]["actions"][0]["to"] = "org.gone"
    screens[0]["links"] = ["docs.gone"]
    flows = copy.deepcopy(VALID["flows"])
    flows[0]["steps"] = ["auth.login", "org.missing"]
    assert run(tmp_path, broken(screens=screens, flows=flows)) == 1
    out = capsys.readouterr().out
    assert "unknown-screen: action submit of screen auth.login goes to org.gone" in out
    assert "unknown-screen: screen auth.login links to docs.gone" in out
    assert "unknown-screen: flow auth.sign-in steps through org.missing" in out
    assert "3 problems" in out


def test_an_unknown_role_is_named_on_a_screen_and_on_a_flow(tmp_path, capsys):
    screens = copy.deepcopy(VALID["screens"])
    screens[1]["roles"] = ["member", "owner"]
    flows = copy.deepcopy(VALID["flows"])
    flows[1]["role"] = "guest"
    assert run(tmp_path, broken(screens=screens, flows=flows)) == 1
    out = capsys.readouterr().out
    assert "unknown-role: screen org.home names role owner" in out
    assert "unknown-role: flow docs.read runs as role guest" in out


def test_an_action_kind_outside_the_set_is_a_problem(tmp_path, capsys):
    screens = copy.deepcopy(VALID["screens"])
    screens[1]["actions"][1]["kind"] = "destroy"
    assert run(tmp_path, broken(screens=screens)) == 1
    out = capsys.readouterr().out
    assert "unknown-action-kind: action theme of screen org.home has kind destroy" in out
    # Counted apart, so the counts still add up to the actions in the map.
    assert "actions: 5 (navigate 1, submit 2, toggle 0, mutate 1, unknown 1)" in out


def test_duplicate_ids_are_named(tmp_path, capsys):
    screens = copy.deepcopy(VALID["screens"])
    screens.append(copy.deepcopy(screens[0]))
    flows = copy.deepcopy(VALID["flows"])
    flows.append(copy.deepcopy(flows[0]))
    assert run(tmp_path, broken(screens=screens, flows=flows)) == 1
    out = capsys.readouterr().out
    assert "duplicate-id: screen auth.login is defined twice" in out
    assert "duplicate-id: flow auth.sign-in is defined twice" in out


def test_missing_fields_are_named_at_every_level(tmp_path, capsys):
    document = copy.deepcopy(VALID)
    del document["base_url"]
    del document["screens"][2]["title"]
    del document["flows"][1]["steps"]
    del document["screens"][1]["actions"][1]["kind"]
    assert run(tmp_path, document) == 1
    out = capsys.readouterr().out
    assert "missing-field: the map has no base_url" in out
    assert "missing-field: screen docs.home has no title" in out
    assert "missing-field: flow docs.read has no steps" in out
    assert "missing-field: action theme of screen org.home has no kind" in out


def test_a_finding_carries_the_path_and_a_rule_name(tmp_path, capsys):
    screens = copy.deepcopy(VALID["screens"])
    screens[0]["links"] = ["docs.gone"]
    path = write_map(tmp_path, broken(screens=screens))
    assert mapcheck.main([path]) == 1
    first = [line for line in capsys.readouterr().out.splitlines() if line.startswith(path)]
    assert first and first[0].startswith(path + ":0 unknown-screen: ")


def test_an_unreadable_map_exits_two(tmp_path, capsys):
    assert mapcheck.main([str(tmp_path / "gone.json")]) == 2
    assert "unreadable" in capsys.readouterr().err
    (tmp_path / "map.json").write_text("{not json", encoding="utf-8")
    assert mapcheck.main([str(tmp_path / "map.json")]) == 2
    assert "unreadable" in capsys.readouterr().err


def test_a_map_that_is_not_an_object_is_one_problem(tmp_path, capsys):
    assert run(tmp_path, ["screens"]) == 1
    assert "malformed: the map is not a JSON object" in capsys.readouterr().out


def test_a_screen_whose_roles_is_not_a_list_is_a_problem_not_a_traceback(tmp_path, capsys):
    """F11: every list is checked before it is walked, so a field of the
    wrong type is an ordinary problem line and exit 1, never a TypeError."""
    screens = copy.deepcopy(VALID["screens"])
    screens[0]["roles"] = 5
    assert run(tmp_path, broken(screens=screens)) == 1
    out = capsys.readouterr().out
    assert "malformed: roles of screen auth.login is not a list" in out
    assert "Traceback" not in out
    # The rest of the map is still checked: one problem, not a stopped run.
    assert "1 problem" in out


@pytest.mark.parametrize("field", ["actions", "states", "links"])
def test_every_screen_list_of_the_wrong_type_is_a_problem(tmp_path, capsys, field):
    screens = copy.deepcopy(VALID["screens"])
    screens[1][field] = "not a list"
    assert run(tmp_path, broken(screens=screens)) == 1
    assert ("malformed: %s of screen org.home is not a list" % field) in capsys.readouterr().out


def test_a_flow_whose_steps_is_not_a_list_is_a_problem(tmp_path, capsys):
    flows = copy.deepcopy(VALID["flows"])
    flows[0]["steps"] = "auth.login"
    assert run(tmp_path, broken(flows=flows)) == 1
    assert "malformed: steps of flow auth.sign-in is not a list" in capsys.readouterr().out


def test_an_id_outside_the_pattern_is_a_problem(tmp_path, capsys):
    """F16: ids are checked, not trusted, because they are printed into a
    Markdown report and joined into file names downstream."""
    screens = copy.deepcopy(VALID["screens"])
    screens[0]["id"] = "../../etc/passwd"
    flows = copy.deepcopy(VALID["flows"])
    flows[1]["id"] = "Docs.Read"
    assert run(tmp_path, broken(screens=screens, flows=flows)) == 1
    out = capsys.readouterr().out
    assert "bad-id: screen id ../../etc/passwd is not " in out
    assert "bad-id: flow id Docs.Read is not " in out


def test_a_newline_in_an_id_cannot_forge_a_problem_line(tmp_path, capsys):
    """F16: a problem is one line, whatever the map says an id is."""
    screens = copy.deepcopy(VALID["screens"])
    screens[0]["id"] = "auth.login\nmap ok"
    path = write_map(tmp_path, broken(screens=screens))
    assert mapcheck.main([path]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert "map ok" not in lines
    assert any("auth.login?map ok" in line for line in lines), lines


def test_a_three_hundred_character_id_is_cut_in_the_problem_line(tmp_path, capsys):
    screens = copy.deepcopy(VALID["screens"])
    screens[0]["id"] = "auth." + "z" * 300
    assert run(tmp_path, broken(screens=screens)) == 1
    out = capsys.readouterr().out
    assert mapcheck.CUT_MARK in out
    assert "z" * (mapcheck.TEXT_CHARS + 1) not in out
