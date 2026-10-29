"""Tests for the extending-vendorfake route fit check against a synthetic route table."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "extending-vendorfake" / "scripts" / "fit_check.py"
spec = importlib.util.spec_from_file_location("fit_check", SCRIPT)
fit_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fit_check)


@pytest.fixture
def routes(tmp_path):
    path = tmp_path / "routes.json"
    path.write_text(
        json.dumps(
            [
                {"method": "GET", "path": "/v3/merchants/{mId}/orders", "operation_id": "GetOrders", "capability": "orders"},
                {"method": "POST", "path": "/v3/merchants/{mId}/orders", "operation_id": "CreateOrder"},
                {"method": "GET", "path": "/v3/merchants/{mId}/orders/{orderId}", "operation_id": "GetOrder"},
            ]
        )
    )
    return path


def write_calls(tmp_path, text):
    path = tmp_path / "calls.txt"
    path.write_text(text)
    return path


def run(routes, calls, *extra):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--routes", str(routes), "--calls", str(calls), *extra],
        capture_output=True,
        text=True,
    )


def rows_for(routes, calls, *extra):
    done = run(routes, calls, "--json", *extra)
    return done, json.loads(done.stdout)


def test_concrete_ids_and_query_strings_fit_a_template(routes, tmp_path):
    calls = write_calls(tmp_path, "GET /v3/merchants/HRV123/orders?limit=10\n")
    done, report = rows_for(routes, calls)
    (row,) = report["rows"]
    assert (row["verdict"], row["operation_id"]) == ("fit", "GetOrders")
    assert done.returncode == 0, done.stderr


def test_a_served_path_under_another_method_is_a_method_mismatch(routes, tmp_path):
    calls = write_calls(tmp_path, "DELETE /v3/merchants/HRV123/orders\n")
    done, report = rows_for(routes, calls)
    (row,) = report["rows"]
    assert row["verdict"] == "method"
    assert row["methods"] == ["GET", "POST"]
    assert done.returncode == 1


def test_an_unserved_path_is_missing_and_fails_the_run(routes, tmp_path):
    calls = write_calls(tmp_path, "GET /v3/merchants/HRV123/shifts\n")
    done = run(routes, calls)
    assert done.returncode == 1
    assert done.stdout.splitlines()[0].split()[0] == "missing"
    assert done.stdout.strip().splitlines()[-1] == "fit_check: 0 fit, 0 method-mismatch, 1 missing"


def test_mount_prefix_is_stripped_only_when_named(routes, tmp_path):
    calls = write_calls(tmp_path, "GET /clover/v3/merchants/HRV123/orders/ORD1\n")
    _, unmounted = rows_for(routes, calls)
    assert unmounted["rows"][0]["verdict"] == "missing"
    done, mounted = rows_for(routes, calls, "--mount", "clover")
    assert (mounted["rows"][0]["verdict"], mounted["rows"][0]["operation_id"]) == ("fit", "GetOrder")
    assert done.returncode == 0, done.stderr


def test_comments_and_blank_lines_are_ignored_and_a_bad_line_exits_2(routes, tmp_path):
    calls = write_calls(tmp_path, "# from the client module\n\nget /v3/merchants/HRV123/orders/\n")
    _, report = rows_for(routes, calls)
    assert [(r["verdict"], r["operation_id"]) for r in report["rows"]] == [("fit", "GetOrders")]
    bad = write_calls(tmp_path, "GET /v3/merchants/HRV123/orders\nv3/merchants/HRV123/orders\n")
    done = run(routes, bad)
    assert done.returncode == 2
    assert "line 2" in done.stderr


def test_json_output_has_the_counts(routes, tmp_path):
    calls = write_calls(
        tmp_path,
        "GET /v3/merchants/HRV123/orders\nPUT /v3/merchants/HRV123/orders/ORD1\nGET /v3/merchants/HRV123/shifts\n",
    )
    done, report = rows_for(routes, calls)
    assert (report["fit"], report["method"], report["missing"]) == (1, 1, 1)
    assert [r["verdict"] for r in report["rows"]] == ["fit", "method", "missing"]
    assert set(report["rows"][0]) == {"call", "verdict", "operation_id", "methods"}
    assert done.returncode == 1


@pytest.mark.parametrize("text", ["", "\n\n", "# only a comment\n\n# and another\n"])
def test_an_empty_inventory_fails_closed_with_exit_2(routes, tmp_path, text):
    calls = write_calls(tmp_path, text)
    for extra in ((), ("--json",)):
        done = run(routes, calls, *extra)
        assert done.returncode == 2, (extra, done.stdout)
        assert done.stdout == ""
        assert "no calls" in done.stderr
