"""Tests for the untangling-test-suites inventory script against a synthetic suite."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "untangling-test-suites" / "scripts" / "inventory.py"
spec = importlib.util.spec_from_file_location("inventory", SCRIPT)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


@pytest.fixture
def suite(tmp_path):
    root = tmp_path / "proj"
    tests = root / "tests"
    (tests / "api").mkdir(parents=True)
    (tests / "ui").mkdir()
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers = ["slow: takes long", "db"]\n')
    (tests / "conftest.py").write_text(
        "import pytest\n"
        "@pytest.fixture(scope='session')\n"
        "def engine():\n    return 1\n"
        "@pytest.fixture\n"
        "def db(engine):\n    return 2\n"
        "@pytest.fixture(autouse=True)\n"
        "def _reset():\n    pass\n"
        "@pytest.fixture\n"
        "def orphan():\n    return 3\n"
    )
    (tests / "api" / "test_orders.py").write_text(
        "import pytest\nfrom tests.helpers import mk\n"
        "@pytest.mark.slow\n"
        "@pytest.mark.parametrize('n', [1, 2, 3])\n"
        "def test_create(db, n):\n    assert db\n"
        "class TestOrders:\n"
        "    @pytest.mark.flaky\n"
        "    def test_list(self, db):\n        pass\n"
    )
    (tests / "api" / "conftest.py").write_text("import pytest\n@pytest.fixture\ndef db():\n    return 'shadow'\n")
    (tests / "ui" / "test_checkout.py").write_text(
        "import pytest\n"
        "async def test_create(page, db):\n    pass\n"
        "def test_other():\n    pass\n"
    )
    (tests / "ui" / "test_broken.py").write_text("def test_x(:\n")
    (tests / "test_top.py").write_text("def test_top(engine):\n    pass\n")
    return root


def test_totals_and_parametrize_cases(suite):
    r = inventory.analyse([suite / "tests"], suite / "pyproject.toml")
    assert r["totals"]["files"] == 6
    assert r["totals"]["tests"] == 5
    assert r["totals"]["cases"] == 7  # 3 parametrized + 4 plain
    assert r["errors"] == [{"path": "ui/test_broken.py", "error": "syntax error line 1"}]


def test_fixtures_duplicates_unused_and_shared(suite):
    r = inventory.analyse([suite / "tests"], suite / "pyproject.toml")
    assert r["duplicates"]["fixtures"] == {"db": ["conftest.py", "api/conftest.py"]}
    assert r["duplicates"]["tests"] == {"test_create": ["api/test_orders.py", "ui/test_checkout.py"]}
    assert r["unused_fixtures"] == ["orphan"]  # autouse _reset is not reported
    assert r["fixtures"]["engine"]["defs"][0]["scope"] == "session"
    assert r["fixtures"]["db"]["used_by"] == 3
    assert r["shared_fixtures"] == ["db"]


def test_markers_registered_and_unregistered(suite):
    r = inventory.analyse([suite / "tests"], suite / "pyproject.toml")
    assert r["markers"]["counts"] == {"flaky": 1, "parametrize": 1, "slow": 1}
    assert r["markers"]["registered"] == ["db", "slow"]
    assert r["markers"]["unregistered"] == ["flaky"]


def test_slices_by_directory(suite, monkeypatch):
    monkeypatch.chdir(suite)
    r = inventory.analyse([suite / "tests"], suite / "pyproject.toml")
    names = [s["slice"] for s in r["slices"]]
    assert names == [".", "api", "ui"]
    api = next(s for s in r["slices"] if s["slice"] == "api")
    assert api["tests"] == 2 and api["command"] == "pytest -q tests/api"


def test_ini_style_config_markers(tmp_path):
    cfg = tmp_path / "pytest.ini"
    cfg.write_text("[pytest]\nmarkers =\n    slow: slow tests\n    integration\n")
    assert inventory.registered_markers(cfg) == {"slow", "integration"}
    assert inventory.registered_markers(None) is None


def test_cli_markdown_and_json(suite):
    r = subprocess.run([sys.executable, str(SCRIPT), "tests"], cwd=suite, capture_output=True, text=True)
    assert r.returncode == 0
    assert "# Test suite inventory" in r.stdout and "`db`" in r.stdout and "Unregistered" in r.stdout
    r = subprocess.run([sys.executable, str(SCRIPT), "tests", "--json"], cwd=suite, capture_output=True, text=True)
    data = json.loads(r.stdout)
    assert data["totals"]["tests"] == 5
    r = subprocess.run([sys.executable, str(SCRIPT), "nope"], cwd=suite, capture_output=True, text=True)
    assert r.returncode == 2


def test_same_tree_same_output(suite):
    a = subprocess.run([sys.executable, str(SCRIPT), "tests", "--json"], cwd=suite, capture_output=True, text=True).stdout
    b = subprocess.run([sys.executable, str(SCRIPT), "tests", "--json"], cwd=suite, capture_output=True, text=True).stdout
    assert a == b


def test_unparseable_files_are_listed_not_fatal(tmp_path):
    t = tmp_path / "tests"; t.mkdir()
    (t / "test_nul.py").write_bytes(b"def test_a():\n    x = '\x00'\n")
    (t / "test_ok.py").write_text("def test_b():\n    pass\n")
    r = inventory.analyse([t], None)
    assert r["totals"]["tests"] == 1
    assert r["errors"][0]["path"] == "test_nul.py" and r["errors"][0]["error"]  # SyntaxError on 3.12+, ValueError before


def test_usefixtures_and_getfixturevalue_count_as_uses(tmp_path):
    t = tmp_path / "tests"; t.mkdir()
    (t / "conftest.py").write_text("import pytest\n@pytest.fixture\ndef seeded():\n    pass\n@pytest.fixture\ndef lazy():\n    pass\n@pytest.fixture\ndef klass():\n    pass\n")
    (t / "test_u.py").write_text(
        "import pytest\n"
        "@pytest.mark.usefixtures('seeded')\ndef test_a():\n    pass\n"
        "def test_b(request):\n    request.getfixturevalue('lazy')\n"
        "@pytest.mark.usefixtures('klass')\nclass TestK:\n    def test_c(self):\n        pass\n"
    )
    r = inventory.analyse([t], None)
    assert r["unused_fixtures"] == []
    assert r["fixtures"]["seeded"]["used_by"] == 1 and r["fixtures"]["lazy"]["used_by"] == 1 and r["fixtures"]["klass"]["used_by"] == 1


def test_tox_ini_without_pytest_section_is_skipped(tmp_path):
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py\n")
    (tmp_path / "setup.cfg").write_text("[tool:pytest]\nmarkers =\n    slow\n")
    assert inventory.find_config(tmp_path).name == "setup.cfg"
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py\n[pytest]\nmarkers = db\n")
    assert inventory.find_config(tmp_path).name == "tox.ini"


def test_base_slice_command_excludes_subdirectories(suite, monkeypatch):
    monkeypatch.chdir(suite)
    r = inventory.analyse([suite / "tests"], suite / "pyproject.toml")
    base = next(s for s in r["slices"] if s["slice"] == ".")
    assert base["command"] == "pytest -q tests --ignore-glob='tests/*/*'"
    import fnmatch
    assert fnmatch.fnmatch("tests/api/test_orders.py", "tests/*/*")
    assert fnmatch.fnmatch("tests/api/deep/test_x.py", "tests/*/*")
    assert not fnmatch.fnmatch("tests/test_top.py", "tests/*/*")


def test_multiple_roots_get_relative_paths_and_root_prefixed_slices(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = tmp_path / "tests"; (a / "api").mkdir(parents=True)
    b = tmp_path / "integration"; b.mkdir()
    (a / "api" / "test_a.py").write_text("def test_a():\n    pass\n")
    (a / "test_top.py").write_text("def test_t():\n    pass\n")
    (b / "test_b.py").write_text("def test_b():\n    pass\n")
    r = inventory.analyse([a, b], None)
    names = [s["slice"] for s in r["slices"]]
    assert names == ["integration", "tests", "tests/api"]
    assert all(not Path(f["path"]).is_absolute() for f in r["files"])
    assert next(s for s in r["slices"] if s["slice"] == "tests/api")["command"] == "pytest -q ./tests/api"



def test_diff_reports_resolved_and_new(suite, tmp_path):
    before = json.loads(subprocess.run([sys.executable, str(SCRIPT), "tests", "--json"], cwd=suite, capture_output=True, text=True).stdout)
    (tmp_path / "before.json").write_text(json.dumps(before))
    (suite / "tests" / "api" / "conftest.py").unlink()
    (suite / "pyproject.toml").write_text('[tool.pytest.ini_options]\nmarkers = ["slow: takes long", "db", "flaky"]\n')
    r = subprocess.run([sys.executable, str(SCRIPT), "tests", "--diff", str(tmp_path / "before.json")], cwd=suite, capture_output=True, text=True)
    assert r.returncode == 0
    assert "## duplicate fixtures: 1 -> 0" in r.stdout and "resolved: `db`" in r.stdout
    assert "## unregistered markers: 1 -> 0" in r.stdout and "resolved: `flaky`" in r.stdout
    assert "| files | 6 | 5 |" in r.stdout
    r = subprocess.run([sys.executable, str(SCRIPT), "tests", "--diff", "nope.json"], cwd=suite, capture_output=True, text=True)
    assert r.returncode == 2
