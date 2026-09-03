"""Unit tests for the governor hook engine.

Transcripts are synthesised in the shape Claude Code writes them (verified
against a real session on 2.1.258): one JSONL line per content block, the
message's usage repeated on each line, subagent transcripts under
``<session>/subagents/agent-<id>.jsonl`` with a ``.meta.json`` beside each.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "bin"
spec = importlib.util.spec_from_file_location("governor", BIN / "governor.py")
governor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(governor)


# --------------------------------------------------------------------------- helpers


def usage(inp=0, out=0, w5=0, w1h=0, read=0):
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": w5 + w1h,
        "cache_read_input_tokens": read,
        "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1h},
    }


def assistant_lines(msg_id, model, use, effort="xhigh", blocks=2, text="", tool_use=None, bash=None):
    """The same message id and usage repeated once per content block."""
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool_use:
        content.append({"type": "tool_use", "id": tool_use[0], "name": tool_use[1], "input": {}})
    if bash:
        content.append({"type": "tool_use", "id": "tb", "name": "Bash", "input": {"command": bash}})
    lines = []
    for i in range(max(blocks, 1)):
        lines.append(json.dumps({
            "type": "assistant",
            "effort": effort,
            "message": {"id": msg_id, "model": model, "role": "assistant", "content": content, "usage": use},
        }))
    return lines


def tool_result_line(tool_use_id, body):
    return json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": body}]}})


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    monkeypatch.setenv("GOVERNOR_STATE_DIR", str(state))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.delenv("GOVERNOR_CONFIG", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    return {"state": state, "project": project, "tmp": tmp_path}


def make_session(tmp: Path, sid="sess1", main_lines=(), agents=None):
    """agents: {agent_id: (meta dict, [lines])}"""
    tp = tmp / f"{sid}.jsonl"
    tp.write_text("\n".join(main_lines) + ("\n" if main_lines else ""))
    if agents:
        sub = tmp / sid / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        for aid, (meta, lines) in agents.items():
            (sub / f"agent-{aid}.jsonl").write_text("\n".join(lines) + "\n")
            (sub / f"agent-{aid}.meta.json").write_text(json.dumps(meta))
    return tp


def ledger_for(tp: Path, sid="sess1"):
    led = governor.Ledger(sid, governor.Pricing.load())
    led.update(str(tp))
    return led


# --------------------------------------------------------------------------- pricing


def test_pricing_resolves_longest_prefix_and_aliases():
    p = governor.Pricing.load()
    assert p.resolve("claude-fable-5-1") == "claude-fable-5-1"
    assert p.resolve("claude-fable-5") == "claude-fable-5"
    assert p.resolve("fable") == "claude-fable-5-1"
    assert p.resolve("claude-sonnet-5") == "claude-sonnet-5"
    assert p.resolve("no-such-model") is None


def test_cost_uses_all_five_rates():
    p = governor.Pricing.load()
    u = usage(inp=1_000_000, out=1_000_000, w5=1_000_000, w1h=1_000_000, read=1_000_000)
    assert p.cost_usd("claude-fable-5-1", u) == pytest.approx(10 + 50 + 12.5 + 20 + 0.25)
    assert p.cost_usd("claude-sonnet-5", u) == pytest.approx(2 + 10 + 2.5 + 4 + 0.2)
    # an unknown model is priced at the dearest known rate, never at zero
    assert p.cost_usd("unknown-model", u) == pytest.approx(10 + 50 + 12.5 + 20 + 0.25)
    assert p.priced_key("unknown-model") == ("claude-fable-5-1", False)


def test_cost_falls_back_when_cache_creation_breakdown_is_absent():
    p = governor.Pricing.load()
    u = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 1_000_000, "cache_read_input_tokens": 0}
    assert p.cost_usd("claude-opus-5", u) == pytest.approx(6.25)


# --------------------------------------------------------------------------- ledger


def test_ledger_counts_each_message_once_despite_repeated_lines(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=1000), blocks=5))
    led = ledger_for(tp)
    t = led.state["models"]["claude-fable-5-1"]
    assert t["messages"] == 1
    assert t["output"] == 1000
    assert led.main_model() == "claude-fable-5-1"
    assert led.state["main_effort"] == "xhigh"


def test_ledger_is_incremental_and_ignores_partial_trailing_line(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=100), blocks=1))
    led = ledger_for(tp)
    led.save()
    with tp.open("a") as f:
        f.write("\n".join(assistant_lines("m2", "claude-fable-5-1", usage(out=200), blocks=1)) + "\n")
        f.write('{"type": "assistant", "message": {"id": "m3", "model": "claude-fable-5-1", "usage": {"output_tokens": 999')  # no newline
    led2 = governor.Ledger("sess1", governor.Pricing.load())
    led2.update(str(tp))
    assert led2.state["models"]["claude-fable-5-1"]["output"] == 300
    assert led2.state["models"]["claude-fable-5-1"]["messages"] == 2


def test_ledger_reads_subagent_transcripts_and_flags_inherited_effort(env):
    agents = {
        "aimpl-1": ({"customAgentType": "implementer", "model": "sonnet"},
                    assistant_lines("s1", "claude-sonnet-5", usage(out=5000), effort="xhigh", blocks=1)),
    }
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=100), blocks=1), agents=agents)
    led = ledger_for(tp)
    assert led.state["agents"]["aimpl-1"]["model"] == "claude-sonnet-5"
    assert led.expensive_spend(governor.DEFAULTS) == pytest.approx(100 * 50 / 1e6)
    assert led.total_spend() == pytest.approx(100 * 50 / 1e6 + 5000 * 10 / 1e6)
    assert "inherited session effort?" in led.report(governor.DEFAULTS)


def test_ledger_attributes_tool_result_bytes_to_tool_names(env):
    lines = assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1, tool_use=("tu1", "Read"))
    lines.append(tool_result_line("tu1", "x" * 5000))
    lines += assistant_lines("m2", "claude-fable-5-1", usage(out=10), blocks=1, tool_use=("tu2", "Bash"))
    lines.append(tool_result_line("tu2", [{"type": "text", "text": "y" * 100}]))
    tp = make_session(env["tmp"], main_lines=lines)
    led = ledger_for(tp)
    assert led.state["tool_results"]["Read"] == 5000
    assert led.state["tool_results"]["Bash"] > 100


# --------------------------------------------------------------------------- config


def test_config_precedence_and_project_files_only_tighten(env, monkeypatch):
    (env["tmp"] / "home" / ".claude" / "governor.json").write_text(json.dumps({"budget_usd": 10, "worker_model": "haiku"}))
    (env["project"] / ".claude" / "governor.json").write_text(json.dumps({"budget_usd": 2, "report_contracts": {"extra": "worker"}}))
    cfg = governor.load_config(str(env["project"]))
    assert cfg["budget_usd"] == 2  # a project may lower the budget
    assert cfg["worker_model"] == "haiku"
    assert cfg["report_contracts"]["extra"] == "worker"
    assert cfg["report_contracts"]["implementer"] == "worker"  # dict merge keeps defaults
    # a project may not raise it, allow forks, drop enforcement or empty the expensive list
    (env["project"] / ".claude" / "governor.json").write_text(json.dumps(
        {"budget_usd": 99, "allow_fork": True, "enforce_reports": False, "enforce_budget": False, "expensive_models": [], "max_expensive_spawns": 50}))
    cfg = governor.load_config(str(env["project"]))
    assert cfg["budget_usd"] == 10 and cfg["allow_fork"] is False and cfg["enforce_reports"] is True
    assert cfg["enforce_budget"] is True and cfg["expensive_models"] == ["fable", "mythos"] and cfg["max_expensive_spawns"] == 3
    assert len(cfg["_ignored"]) == 6 and all("loosen" in n for n in cfg["_ignored"])
    # the user's own file may raise it for this project, and $GOVERNOR_CONFIG may set anything
    (env["tmp"] / "home" / ".claude" / "governor.json").write_text(json.dumps(
        {"budget_usd": 10, "projects": {str(env["project"].resolve()): {"budget_usd": 40, "allow_fork": True}}}))
    cfg = governor.load_config(str(env["project"]))
    assert cfg["budget_usd"] == 40 and cfg["allow_fork"] is True
    override = env["tmp"] / "o.json"
    override.write_text(json.dumps({"budget_usd": 3, "allow_fork": True}))
    monkeypatch.setenv("GOVERNOR_CONFIG", str(override))
    cfg = governor.load_config(str(env["project"]))
    assert cfg["budget_usd"] == 3 and cfg["allow_fork"] is True


def test_config_values_are_validated_not_trusted(env):
    (env["project"] / ".claude" / "governor.json").write_text(
        '{"budget_usd": null, "warn_at": "0.7", "allow_fork": "yes", "expensive_models": "fable", "worker_model": "fable", "nope": 1, "max_report_blocks": 1e999}')
    cfg = governor.load_config(str(env["project"]))
    assert cfg["budget_usd"] == 15.0 and cfg["warn_at"] == 0.7 and cfg["allow_fork"] is False
    assert cfg["expensive_models"] == ["fable", "mythos"] and cfg["worker_model"] == "sonnet"
    assert cfg["max_report_blocks"] == 2
    assert len(cfg["_ignored"]) == 7
    (env["project"] / ".claude" / "governor.json").write_text("not json")
    cfg = governor.load_config(str(env["project"]))
    assert cfg["budget_usd"] == 15.0 and any("unreadable" in n for n in cfg["_ignored"])


# --------------------------------------------------------------------------- agent policy


def fable_session(env):
    return ledger_for(make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1)))


def test_model_less_spawn_is_pinned_to_worker_model(env):
    led = fable_session(env)
    d = governor.agent_policy({"subagent_type": "general-purpose", "prompt": "do x"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "rewrite"
    assert d["updated_input"]["model"] == "sonnet"
    assert d["updated_input"]["prompt"] == "do x"


def test_inherit_is_treated_as_model_less(env):
    led = fable_session(env)
    d = governor.agent_policy({"subagent_type": "x", "model": "inherit", "prompt": "p"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "rewrite"


def test_explicit_cheap_model_passes_untouched(env):
    led = fable_session(env)
    d = governor.agent_policy({"subagent_type": "x", "model": "opus", "prompt": "p"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "allow"
    assert d["model"] == "opus"


def test_project_agent_with_pinned_model_is_respected(env):
    (env["project"] / ".claude" / "agents").mkdir()
    (env["project"] / ".claude" / "agents" / "deep-reviewer.md").write_text("---\nname: deep-reviewer\nmodel: opus\n---\nbody\n")
    led = fable_session(env)
    d = governor.agent_policy({"subagent_type": "deep-reviewer", "prompt": "p"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "allow" and d["model"] == "opus"


def test_project_agent_with_inherit_is_pinned(env):
    (env["project"] / ".claude" / "agents").mkdir()
    (env["project"] / ".claude" / "agents" / "inh.md").write_text("---\nname: inh\nmodel: inherit\n---\nbody\n")
    led = fable_session(env)
    d = governor.agent_policy({"subagent_type": "inh", "prompt": "p"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "rewrite"


def test_plugin_own_agents_are_found_with_namespace_prefix(env):
    led = fable_session(env)
    d = governor.agent_policy({"subagent_type": "governor:scout", "prompt": "p"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "allow"
    assert d["model"] == "haiku"


def test_fork_is_denied_by_default_and_allowed_by_config(env):
    led = fable_session(env)
    d = governor.agent_policy({"subagent_type": "fork", "prompt": "p"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "deny" and "fork" in d["reason"]
    cfg = dict(governor.DEFAULTS, allow_fork=True)
    d = governor.agent_policy({"subagent_type": "fork", "prompt": "p"}, cfg, led, str(env["project"]))
    assert d["action"] != "deny"


def test_expensive_spawn_needs_a_brief(env):
    led = fable_session(env)
    d = governor.agent_policy({"subagent_type": "governor:architect", "prompt": "just think about it"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "deny"
    assert "## Question" in d["reason"] and "## Definition of done" in d["reason"]
    brief = "## Question\nA or B?\n## Context\nsee src/x.py\n## Definition of done\nA decision with reasons."
    d = governor.agent_policy({"subagent_type": "governor:architect", "prompt": brief}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "allow"


def test_expensive_spawn_brief_size_cap(env):
    led = fable_session(env)
    brief = "## Question\nq\n## Context\n" + "x" * 9000 + "\n## Definition of done\nd"
    d = governor.agent_policy({"subagent_type": "x", "model": "fable", "prompt": brief}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "deny" and "chars" in d["reason"]


def test_expensive_spawn_count_cap(env):
    led = fable_session(env)
    led.state["expensive_spawns"] = 3
    brief = "## Question\nq\n## Context\nc\n## Definition of done\nd"
    d = governor.agent_policy({"subagent_type": "x", "model": "fable", "prompt": brief}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "deny" and "limit 3" in d["reason"]


def test_always_pin_workers_false_lets_cheap_sessions_inherit(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-sonnet-5", usage(out=10), blocks=1))
    led = ledger_for(tp)
    cfg = dict(governor.DEFAULTS, always_pin_workers=False)
    d = governor.agent_policy({"subagent_type": "general-purpose", "prompt": "p"}, cfg, led, str(env["project"]))
    assert d["action"] == "allow"


# --------------------------------------------------------------------------- pre-tool-use hook


def hook_base(tp, sid="sess1"):
    return {"session_id": sid, "transcript_path": str(tp), "cwd": ".", "hook_event_name": "PreToolUse"}


def test_budget_gate_denies_when_expensive_spend_reaches_budget(env):
    # 400k Fable output tokens = $20 > $15 default budget
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=400_000), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={"file_path": "x"}), governor.DEFAULTS, led, str(env["project"]))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "/model opus" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_budget_gate_lifts_after_model_switch(env):
    lines = assistant_lines("m1", "claude-fable-5-1", usage(out=400_000), blocks=1)
    lines += assistant_lines("m2", "claude-opus-5", usage(out=10), blocks=1)
    tp = make_session(env["tmp"], main_lines=lines)
    led = governor.Ledger("sess1", governor.Pricing.load())
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={}), governor.DEFAULTS, led, str(env["project"]))
    assert out == {}


def test_budget_warning_fires_once(env):
    # 220k output tokens = $11 = 73% of $15
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=220_000), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={}), governor.DEFAULTS, led, str(env["project"]))
    assert "systemMessage" in out
    out2 = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={}), governor.DEFAULTS, led, str(env["project"]))
    assert out2 == {}


def test_under_budget_non_agent_tool_is_silent(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Bash", tool_input={"command": "ls"}), governor.DEFAULTS, led, str(env["project"]))
    assert out == {}


def test_agent_rewrite_goes_out_as_updated_input(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Agent", tool_input={"subagent_type": "general-purpose", "prompt": "p", "description": "d"}), governor.DEFAULTS, led, str(env["project"]))
    hso = out["hookSpecificOutput"]
    assert "permissionDecision" not in hso
    assert hso["updatedInput"]["model"] == "sonnet"
    assert led.state["spawns"][-1]["action"] == "rewrite"


def test_expensive_spawn_increments_counter_only_when_allowed(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    brief = "## Question\nq\n## Context\nc\n## Definition of done\nd"
    governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Agent", tool_input={"subagent_type": "x", "model": "fable", "prompt": "no brief"}), governor.DEFAULTS, led, str(env["project"]))
    assert led.state["expensive_spawns"] == 0
    governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Agent", tool_input={"subagent_type": "x", "model": "fable", "prompt": brief}), governor.DEFAULTS, led, str(env["project"]))
    assert led.state["expensive_spawns"] == 1


# --------------------------------------------------------------------------- report contracts

GOOD_WORKER = """## Result
DONE

## Changed files
- src/a.py

## Evidence
```
$ pytest -q tests/test_a.py
3 passed in 0.4s
```
"""


def test_worker_report_contract():
    assert governor.report_problems(GOOD_WORKER, "worker") == []
    assert any("Result" in p for p in governor.report_problems("## Changed files\n- a\n## Evidence\n```\n$ x\nok\n```", "worker"))
    assert any("DONE" in p for p in governor.report_problems("## Result\nit went fine\n## Changed files\nnone\n## Evidence\n```\n$ x\nok\n```", "worker"))
    assert any("Evidence" in p for p in governor.report_problems("## Result\nDONE\n## Changed files\nnone\n## Evidence\ntests pass, trust me", "worker"))
    assert any("'$ '" in p for p in governor.report_problems("## Result\nDONE\n## Changed files\nnone\n## Evidence\n```\n3 passed\n```", "worker"))
    assert governor.report_problems("## Result: BLOCKED\nwhy\n## Changed files\nnone\n## Evidence\n```\n$ pytest\n1 failed\n```", "worker") == []


def test_scout_and_reviewer_contracts():
    assert governor.report_problems("## Findings\n- src/x.py:12 does y", "scout") == []
    assert governor.report_problems("## Findings\n- somewhere in the code", "scout")
    ok = 'done\n```json\n{"findings": [{"file": "a.py", "failure_scenario": "x"}]}\n```'
    assert governor.report_problems(ok, "reviewer") == []
    assert governor.report_problems('```json\n{"findings": [{"file": "a.py"}]}\n```', "reviewer")
    assert governor.report_problems('no json at all', "reviewer")


def test_subagent_stop_blocks_then_gives_up(env):
    agents = {"aimpl-1": ({"customAgentType": "governor:implementer"}, assistant_lines("s1", "claude-sonnet-5", usage(out=5), blocks=1, text="I finished, tests pass."))}
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1), agents=agents)
    led = governor.Ledger("sess1", governor.Pricing.load())
    hook = {"session_id": "sess1", "transcript_path": str(tp), "agent_id": "aimpl-1", "hook_event_name": "SubagentStop"}
    out1 = governor.h_subagent_stop(hook, governor.DEFAULTS, led)
    assert out1["decision"] == "block" and "Evidence" in out1["reason"]
    out2 = governor.h_subagent_stop(hook, governor.DEFAULTS, led)
    assert out2["decision"] == "block"
    out3 = governor.h_subagent_stop(hook, governor.DEFAULTS, led)
    assert "decision" not in out3 and "systemMessage" in out3


def test_subagent_stop_accepts_good_report_and_ignores_unknown_agents(env):
    agents = {
        "aimpl-2": ({"customAgentType": "governor:implementer"}, assistant_lines("s1", "claude-sonnet-5", usage(out=5), blocks=1, text=GOOD_WORKER, bash="pytest -q tests/test_a.py")),
        "aother": ({"customAgentType": "researcher"}, assistant_lines("s2", "claude-sonnet-5", usage(out=5), blocks=1, text="whatever")),
    }
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1), agents=agents)
    led = governor.Ledger("sess1", governor.Pricing.load())
    assert governor.h_subagent_stop({"session_id": "sess1", "transcript_path": str(tp), "agent_id": "aimpl-2"}, governor.DEFAULTS, led) == {}
    assert governor.h_subagent_stop({"session_id": "sess1", "transcript_path": str(tp), "agent_id": "aother"}, governor.DEFAULTS, led) == {}


def test_subagent_stop_uses_agent_type_and_transcript_from_hook_when_present(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    at = env["tmp"] / "elsewhere.jsonl"
    at.write_text("\n".join(assistant_lines("s9", "claude-sonnet-5", usage(out=5), blocks=1, text="no report")) + "\n")
    led = governor.Ledger("sess1", governor.Pricing.load())
    out = governor.h_subagent_stop({"session_id": "sess1", "transcript_path": str(tp), "agent_id": "zzz", "agent_type": "governor:scout", "agent_transcript_path": str(at)}, governor.DEFAULTS, led)
    assert out["decision"] == "block" and "Findings" in out["reason"]


# --------------------------------------------------------------------------- session hooks and CLI


def test_session_start_injects_policy_and_readout(env):
    tp = make_session(env["tmp"], main_lines=[])
    led = governor.Ledger("sess1", governor.Pricing.load())
    out = governor.h_session_start({"session_id": "sess1", "transcript_path": str(tp)}, governor.DEFAULTS, led)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "[governor]" in ctx and "governor" in ctx.lower()
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_session_end_appends_history(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=1000), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    governor.h_session_end({"session_id": "sess1", "transcript_path": str(tp), "reason": "exit"}, governor.DEFAULTS, led)
    rows = [json.loads(l) for l in (env["state"] / "history.jsonl").read_text().splitlines()]
    assert rows[-1]["main_model"] == "claude-fable-5-1"
    assert rows[-1]["expensive_usd"] == pytest.approx(0.05)


def run_cli(env, args, stdin=None, extra_env=None):
    return subprocess.run([sys.executable, str(BIN / "governor.py"), *args], input=stdin, capture_output=True, text=True,
                          env={**os.environ, "GOVERNOR_STATE_DIR": str(env["state"]), "CLAUDE_PROJECT_DIR": str(env["project"]), "HOME": str(env["tmp"] / "home"), **(extra_env or {})})


# Bytes no UTF-8 decoder accepts (a cp1252 e-acute); PYTHONUTF8=1 pins the
# child's decoder so the test does not depend on the machine's locale.
NOT_UTF8 = b"## Result\nDONE caf\xe9\n"
UTF8_ENV = {"PYTHONUTF8": "1"}


def test_cli_hook_roundtrip_and_fail_open(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    r = run_cli(env, ["pre-tool-use"], stdin=json.dumps({"session_id": "sess1", "transcript_path": str(tp), "tool_name": "Agent", "tool_input": {"subagent_type": "fork", "prompt": "p"}}))
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    r = run_cli(env, ["pre-tool-use"], stdin="not json")
    assert r.returncode == 0 and r.stdout.strip() in ("", "{}")
    r = run_cli(env, ["no-such-event"], stdin="{}")
    assert r.returncode == 2


def test_cli_status_and_budget(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=1000), blocks=1))
    run_cli(env, ["user-prompt"], stdin=json.dumps({"session_id": "sess1", "transcript_path": str(tp), "prompt": "hi"}))
    r = run_cli(env, ["status"])
    assert "claude-fable-5-1" in r.stdout and "Budget" in r.stdout
    r = run_cli(env, ["budget", "set", "42"])
    assert r.returncode == 0, r.stdout + r.stderr
    user_file = json.loads((env["tmp"] / "home" / ".claude" / "governor.json").read_text())
    assert user_file["projects"][str(env["project"].resolve())]["budget_usd"] == 42
    r = run_cli(env, ["budget", "show"])
    assert "budget_usd: 42.0" in r.stdout
    # a project file can only lower; writing a raise there is reported, not silently accepted
    r = run_cli(env, ["budget", "set", "5", "--project"])
    assert r.returncode == 0 and json.loads((env["project"] / ".claude" / "governor.json").read_text())["budget_usd"] == 5
    r = run_cli(env, ["budget", "set", "50", "--project"])
    assert r.returncode == 1 and "another config file wins" in r.stdout
    for bad in ("nan", "inf", "-1", "abc"):
        assert run_cli(env, ["budget", "set", bad]).returncode == 2


# --------------------------------------------------------------------------- hook-input corrections (spec fetched 2026-09-02)


def test_subagent_tool_calls_are_gated_on_their_own_model(env):
    agents = {"aw": ({"customAgentType": "implementer"}, assistant_lines("s1", "claude-sonnet-5", usage(out=5), blocks=1))}
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=400_000), blocks=1), agents=agents)
    led = governor.Ledger("sess1", governor.Pricing.load())
    # main thread: over budget -> denied
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={}), governor.DEFAULTS, led, str(env["project"]))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    # the Sonnet worker's own call: allowed
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={}, agent_id="aw", agent_type="implementer"), governor.DEFAULTS, led, str(env["project"]))
    assert out == {}


def test_session_start_model_and_effort_come_from_hook_input(env):
    tp = make_session(env["tmp"], main_lines=[])
    led = governor.Ledger("sess1", governor.Pricing.load())
    governor.h_session_start({"session_id": "sess1", "transcript_path": str(tp), "source": "startup", "model": "claude-fable-5-1", "effort": {"level": "xhigh"}}, governor.DEFAULTS, led)
    assert led.main_model() == "claude-fable-5-1"
    assert led.state["main_effort"] == "xhigh"
    d = governor.agent_policy({"subagent_type": "general-purpose", "prompt": "p"}, dict(governor.DEFAULTS, always_pin_workers=False), led, str(env["project"]))
    assert d["action"] == "rewrite"  # the session is known to be expensive before any message exists


def test_subagent_stop_prefers_last_assistant_message_from_hook(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    hook = {"session_id": "sess1", "transcript_path": str(tp), "agent_id": "nope", "agent_type": "governor:implementer", "last_assistant_message": GOOD_WORKER}
    assert governor.h_subagent_stop(hook, governor.DEFAULTS, led) == {}
    hook["last_assistant_message"] = "done, trust me"
    assert governor.h_subagent_stop(hook, governor.DEFAULTS, led)["decision"] == "block"


def test_subagent_model_env_var_counts_as_declared(env, monkeypatch):
    led = fable_session(env)
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", "haiku")
    d = governor.agent_policy({"subagent_type": "general-purpose", "prompt": "p"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "allow" and d["model"] == "haiku"
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", "fable")
    d = governor.agent_policy({"subagent_type": "general-purpose", "prompt": "p"}, governor.DEFAULTS, led, str(env["project"]))
    assert d["action"] == "deny"


def test_state_dir_precedence(env, monkeypatch):
    monkeypatch.delenv("GOVERNOR_STATE_DIR")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(env["tmp"] / "plugdata"))
    assert governor.state_dir() == env["tmp"] / "plugdata"
    governor.STATE_DIR_ARG = "${CLAUDE_PLUGIN_DATA}"  # unsubstituted placeholder must be ignored
    assert governor.state_dir() == env["tmp"] / "plugdata"
    governor.STATE_DIR_ARG = str(env["tmp"] / "argdir")
    assert governor.state_dir() == env["tmp"] / "argdir"
    governor.STATE_DIR_ARG = None
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA")
    assert governor.state_dir() == env["tmp"] / "home" / ".cache" / "governor"


def test_cli_state_dir_flag(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    target = env["tmp"] / "viaflag"
    r = subprocess.run([sys.executable, str(BIN / "governor.py"), "user-prompt", "--state-dir", str(target)], input=json.dumps({"session_id": "s9", "transcript_path": str(tp)}), capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k != "GOVERNOR_STATE_DIR"} | {"HOME": str(env["tmp"] / "home"), "CLAUDE_PROJECT_DIR": str(env["project"])})
    assert r.returncode == 0, r.stderr
    assert (target / "sessions" / "s9.json").exists()


def test_subagent_transcript_path_is_mapped_back_to_the_session(env):
    agents = {"aw": ({"customAgentType": "implementer"}, assistant_lines("s1", "claude-sonnet-5", usage(out=5), blocks=1))}
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1), agents=agents)
    sub = tp.with_suffix("") / "subagents" / "agent-aw.jsonl"
    led = governor.Ledger("sess1", governor.Pricing.load())
    led.update(str(sub))  # a hook fired inside the worker hands over the worker's file
    assert led.main_model() == "claude-fable-5-1"
    assert led.state["agents"]["aw"]["model"] == "claude-sonnet-5"
    assert governor.Ledger.main_transcript(str(tp)) == tp


def test_foreign_plugin_agent_found_via_install_registry(env):
    install = env["tmp"] / "cache" / "mk" / "other-plugin" / "1.0.0"
    (install / "agents").mkdir(parents=True)
    (install / "agents" / "worker.md").write_text("---\nname: worker\nmodel: opus\neffort: low\n---\nbody\n")
    (env["tmp"] / "home" / ".claude" / "plugins").mkdir(parents=True)
    (env["tmp"] / "home" / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"other-plugin@mk": [{"scope": "user", "installPath": str(install)}]}}))
    led = fable_session(env)
    cfg = dict(governor.DEFAULTS, worker_model="haiku")
    d = governor.agent_policy({"subagent_type": "other-plugin:worker", "prompt": "p"}, cfg, led, str(env["project"]))
    assert d["action"] == "allow" and d["model"] == "opus"


def test_sibling_plugin_agent_found_in_checkout(env):
    # plugins/py-testing/agents/test-implementer.md sits beside plugins/governor in this repo
    led = fable_session(env)
    cfg = dict(governor.DEFAULTS, worker_model="haiku")
    d = governor.agent_policy({"subagent_type": "py-testing:test-implementer", "prompt": "p"}, cfg, led, str(env["project"]))
    assert d["action"] == "allow" and d["model"] == "sonnet"
    # an unknown plugin still falls through to the rewrite
    d = governor.agent_policy({"subagent_type": "nope:worker", "prompt": "p"}, cfg, led, str(env["project"]))
    assert d["action"] == "rewrite" and d["model"] == "haiku"



# --------------------------------------------------------------------------- review-round fixes


def test_zero_budget_closes_the_gate_and_cheap_delegation_stays_open(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    cfg = dict(governor.DEFAULTS, budget_usd=0.0)
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={}), cfg, led, str(env["project"]))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    # delegating to a cheap worker is the one thing still allowed
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Agent", tool_input={"subagent_type": "governor:implementer", "prompt": "p"}), cfg, led, str(env["project"]))
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    # but a fork or an expensive spawn is not
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Agent", tool_input={"subagent_type": "fork", "prompt": "p"}), cfg, led, str(env["project"]))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    # and an explicit opt-out is the only way to have no gate
    cfg = dict(governor.DEFAULTS, budget_usd=0.0, enforce_budget=False)
    assert governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={}), cfg, led, str(env["project"])) == {}


def test_unknown_expensive_model_is_charged_and_flagged(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-6", usage(out=400_000), blocks=1))
    led = ledger_for(tp)
    assert led.expensive_spend(governor.DEFAULTS) == pytest.approx(20.0)
    assert led.state["unpriced_models"] == ["claude-fable-6"]
    assert "unpriced" in led.readout(governor.DEFAULTS) and "claude-fable-6" in led.report(governor.DEFAULTS)
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={}), governor.DEFAULTS, governor.Ledger("sess1", governor.Pricing.load()), str(env["project"]))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_cache_write_fallback_never_goes_negative():
    w5, w1h = governor.split_cache_writes({"cache_creation": {"ephemeral_1h_input_tokens": 400_000}})
    assert (w5, w1h) == (0, 400_000)
    p = governor.Pricing.load()
    assert p.cost_usd("claude-fable-5-1", {"cache_creation": {"ephemeral_1h_input_tokens": 400_000}}) == pytest.approx(8.0)


def test_evidence_must_have_been_executed(env):
    ran = ["pytest -q tests/test_a.py", "ls -la"]
    assert governor.report_problems(GOOD_WORKER, "worker", ran) == []
    fake = GOOD_WORKER.replace("pytest -q tests/test_a.py", "pytest -q tests/test_never.py")
    probs = governor.report_problems(fake, "worker", ran)
    assert probs and "never ran" in probs[0]
    assert governor.report_problems(fake, "worker", None) == []  # no transcript: shape only
    # a pipeline whose head command ran is accepted
    piped = GOOD_WORKER.replace("pytest -q tests/test_a.py", "pytest -q tests/test_a.py | tail -3")
    assert governor.report_problems(piped, "worker", ran) == []


def test_subagent_stop_blocks_fabricated_evidence(env):
    agents = {"aw": ({"customAgentType": "governor:implementer"}, assistant_lines("s1", "claude-sonnet-5", usage(out=5), blocks=1, text=GOOD_WORKER, bash="echo hi"))}
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1), agents=agents)
    led = governor.Ledger("sess1", governor.Pricing.load())
    out = governor.h_subagent_stop({"session_id": "sess1", "transcript_path": str(tp), "agent_id": "aw", "agent_type": "governor:implementer", "last_assistant_message": GOOD_WORKER}, governor.DEFAULTS, led)
    assert out["decision"] == "block" and "never ran" in out["reason"]


def test_contract_lookup_respects_namespaces():
    cfg = governor.DEFAULTS
    assert governor.contract_for("governor:implementer", cfg) == "worker"
    assert governor.contract_for("py-testing:test-implementer", cfg) == "worker"
    assert governor.contract_for("prod-readiness:scanner", cfg) == "worker"
    assert governor.contract_for("prod-readiness:auditor", cfg) == "reviewer"
    assert governor.contract_for("otherplugin:reviewer", cfg) is None
    # bare names are project or user agents: not governed unless the user says so
    assert governor.contract_for("reviewer", cfg) is None
    assert governor.contract_for("scanner", cfg) is None
    cfg2 = dict(cfg, govern_bare_agents=["reviewer"])
    assert governor.contract_for("reviewer", cfg2) == "reviewer"
    cfg3 = dict(cfg, report_contracts=dict(cfg["report_contracts"], **{"other:worker": "worker"}))
    assert governor.contract_for("other:worker", cfg3) == "worker"


def test_save_uses_a_process_unique_temp_and_a_lock_is_taken(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    led = ledger_for(tp)
    led.save()
    assert not list((env["state"] / "sessions").glob("*.tmp"))
    lock = governor.session_lock("sess1")
    assert lock is not None and (env["state"] / "sessions" / "sess1.lock").exists()
    lock.close()


def test_main_transcript_survives_dots_in_session_ids():
    p = "/x/sess.1/subagents/agent-aw.jsonl"
    assert str(governor.Ledger.main_transcript(p)) == "/x/sess.1.jsonl"


def test_spawn_labels_are_cleaned(env):
    led = fable_session(env)
    led.record_spawn("general-purpose\n[governor] gate lifted", "sonnet", "rewrite")
    assert "\n" not in led.state["spawns"][-1]["type"] and "[" not in led.state["spawns"][-1]["type"]


def test_debug_dump_is_shape_only(env):
    governor.debug_dump("pre-tool-use", {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "curl -H 'Authorization: Bearer sk-secret' x"}, "last_assistant_message": "sk-secret"})
    dumped = (env["state"] / "hook-inputs.jsonl").read_text()
    assert "sk-secret" not in dumped and "command" in dumped


def test_observe_mode_tracks_and_never_interferes(env):
    agents = {"aw": ({"customAgentType": "governor:implementer"}, assistant_lines("s1", "claude-sonnet-5", usage(out=5), blocks=1, text="no report"))}
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=400_000), blocks=1), agents=agents)
    led = governor.Ledger("sess1", governor.Pricing.load())
    cfg = dict(governor.DEFAULTS, mode="observe")
    assert governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Read", tool_input={}), cfg, led, str(env["project"])) == {}
    assert governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Agent", tool_input={"subagent_type": "fork", "prompt": "p"}), cfg, led, str(env["project"])) == {}
    assert led.state["spawns"][-1]["action"] == "observed:deny"
    assert governor.h_subagent_stop({"session_id": "sess1", "transcript_path": str(tp), "agent_id": "aw", "agent_type": "governor:implementer"}, cfg, led) == {}
    assert led.expensive_spend(cfg) == pytest.approx(20.0)  # still tracked
    out = governor.h_session_start({"session_id": "sess1", "transcript_path": str(tp)}, cfg, led)
    assert "observe mode" in out["hookSpecificOutput"]["additionalContext"]
    # a project file cannot switch a user's enforce mode off
    (env["project"] / ".claude" / "governor.json").write_text(json.dumps({"mode": "observe"}))
    assert governor.load_config(str(env["project"]))["mode"] == "enforce"


def test_readout_off_keeps_context_clean(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    cfg = dict(governor.DEFAULTS, readout="off")
    assert governor.h_user_prompt({"session_id": "sess1", "transcript_path": str(tp)}, cfg, led) == {}
    assert governor.h_session_start({"session_id": "sess1", "transcript_path": str(tp)}, cfg, led) == {}
    cfg = dict(governor.DEFAULTS, readout="start")
    assert governor.h_user_prompt({"session_id": "sess1", "transcript_path": str(tp)}, cfg, led) == {}
    assert "additionalContext" in governor.h_session_start({"session_id": "sess1", "transcript_path": str(tp)}, cfg, led)["hookSpecificOutput"]


def test_rewrite_sends_updated_input_without_approving_by_default(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=10), blocks=1))
    led = governor.Ledger("sess1", governor.Pricing.load())
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Agent", tool_input={"subagent_type": "general-purpose", "prompt": "p"}), governor.DEFAULTS, led, str(env["project"]))
    assert "permissionDecision" not in out["hookSpecificOutput"] and out["hookSpecificOutput"]["updatedInput"]["model"] == "sonnet"
    cfg = dict(governor.DEFAULTS, rewrite_decision="allow")
    out = governor.h_pre_tool_use(dict(hook_base(tp), tool_name="Agent", tool_input={"subagent_type": "general-purpose", "prompt": "p"}), cfg, led, str(env["project"]))
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_statusline_reads_saved_state_only(env):
    tp = make_session(env["tmp"], main_lines=assistant_lines("m1", "claude-fable-5-1", usage(out=100_000), blocks=1))
    run_cli(env, ["user-prompt"], stdin=json.dumps({"session_id": "sess1", "transcript_path": str(tp)}))
    r = run_cli(env, ["statusline"], stdin=json.dumps({"session_id": "sess1", "model": {"display_name": "Fable"}, "cost": {"total_cost_usd": 5.5}, "context_window": {"used_percentage": 42.7}}))
    assert r.returncode == 0
    assert r.stdout.strip() == "governor Fable · fable $5.00/$15 · total $5.00 · claude $5.50 · ctx 42%"
    r = run_cli(env, ["statusline"], stdin="not json")
    assert r.returncode == 0 and r.stdout.startswith("governor")
    r = run_cli(env, ["statusline-snippet"])
    assert json.loads(r.stdout)["statusLine"]["type"] == "command"



# --------------------------------------------------------------------------- deterministic helpers


def test_check_report_cli(env, tmp_path):
    good = tmp_path / "good.md"; good.write_text(GOOD_WORKER)
    r = run_cli(env, ["check-report", str(good), "--contract", "worker"])
    assert r.returncode == 0 and r.stdout.startswith("OK contract=worker result=DONE"), r.stdout
    bad = tmp_path / "bad.md"; bad.write_text("## Result\nDONE\n## Changed files\n- a\n## Evidence\ntrust me")
    r = run_cli(env, ["check-report", str(bad), "--contract", "worker"])
    assert r.returncode == 1 and "NONCOMPLIANT" in r.stdout and "Evidence" in r.stdout
    r = run_cli(env, ["check-report", "-", "--contract", "scout"], stdin="## Findings\n- src/x.py:3 thing")
    assert r.returncode == 0
    # An unreadable report is a NONCOMPLIANT verdict, never a silent exit 0.
    cp = tmp_path / "cp1252.md"; cp.write_bytes(NOT_UTF8)
    r = run_cli(env, ["check-report", str(cp), "--contract", "worker"], extra_env=UTF8_ENV)
    assert r.returncode == 1 and r.stdout.startswith("NONCOMPLIANT contract=worker result=?\n- cannot read "), r.stdout + r.stderr
    r = run_cli(env, ["check-report", str(tmp_path / "missing.md"), "--contract", "worker"])
    assert r.returncode == 1 and "- cannot read" in r.stdout


def test_plan_levels_and_errors():
    slices = [
        {"id": "shared", "files": ["tests/conftest.py"], "deps": [], "command": "pytest -q tests/_support"},
        {"id": "api", "files": ["tests/api/test_a.py"], "deps": ["shared"], "command": "pytest -q tests/api"},
        {"id": "ui", "files": ["tests/ui/test_u.py"], "deps": ["shared"], "command": "pytest -q tests/ui"},
        {"id": "e2e", "files": ["tests/e2e/test_e.py"], "deps": ["api", "ui"], "command": "pytest -q tests/e2e"},
    ]
    levels, errors = governor.plan_levels(slices)
    assert errors == [] and levels == [["shared"], ["api", "ui"], ["e2e"]]
    assert "cycle" in governor.plan_levels([{"id": "a", "deps": ["b"]}, {"id": "b", "deps": ["a"]}])[1][0]
    assert "unknown dependency" in governor.plan_levels([{"id": "a", "deps": ["zzz"]}])[1][0]
    assert "both change x.py" in governor.plan_levels([{"id": "a", "files": ["x.py"]}, {"id": "b", "files": ["x.py"]}])[1][0]
    md = governor.render_plan("p", slices, levels)
    assert "## Level 2" in md and "`pytest -q tests/e2e`" in md


def test_plan_cli_writes_files(env, tmp_path):
    sl = tmp_path / "slices.json"
    sl.write_text(json.dumps([{"id": "a", "files": ["a.py"], "command": "pytest -q a"}, {"id": "b", "deps": ["a"], "files": ["b.py"], "command": "pytest -q b"}]))
    r = run_cli(env, ["plan", "build", str(sl), "--out", str(tmp_path / "gov")])
    assert r.returncode == 0 and "2 slices in 2 levels" in r.stdout
    plan = json.loads((tmp_path / "gov" / "plan.json").read_text())
    assert plan["levels"] == [["a"], ["b"]] and (tmp_path / "gov" / "plan.md").exists()
    r = run_cli(env, ["plan", "check", str(tmp_path / "gov" / "plan.json")])
    assert r.returncode == 0 and r.stdout.startswith("PLAN OK")
    bad = tmp_path / "bad.json"; bad.write_text(json.dumps([{"id": "a", "deps": ["a"]}]))
    r = run_cli(env, ["plan", "build", str(bad), "--out", str(tmp_path / "gov2")])
    assert r.returncode == 1 and "PLAN INVALID" in r.stdout


def test_run_worker_dry_run_and_fake_claude(env, tmp_path):
    spec = tmp_path / "slice.md"; spec.write_text("# Spec: slice\nGoal.\n")
    r = run_cli(env, ["run-worker", "--spec", str(spec), "--dry-run", "--out", str(tmp_path / "runs")])
    assert r.returncode == 0 and "--max-budget-usd 2.0" in r.stdout and "--agent governor:implementer" in r.stdout and "--plugin-dir" in r.stdout
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    fake = fake_bin / "claude"
    fake.write_text("#!/bin/sh\ncat > /dev/null\nprintf '%s' \"$FAKE_REPORT\"\n")
    fake.chmod(0o755)
    base_env = {**os.environ, "GOVERNOR_STATE_DIR": str(env["state"]), "CLAUDE_PROJECT_DIR": str(env["project"]), "HOME": str(env["tmp"] / "home"), "PATH": str(fake_bin) + ":" + os.environ["PATH"]}
    r = subprocess.run([sys.executable, str(BIN / "governor.py"), "run-worker", "--spec", str(spec), "--out", str(tmp_path / "runs")],
                       capture_output=True, text=True, env={**base_env, "FAKE_REPORT": GOOD_WORKER})
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("VERDICT: DONE")
    assert list((tmp_path / "runs").glob("slice-*.md"))
    r = subprocess.run([sys.executable, str(BIN / "governor.py"), "run-worker", "--spec", str(spec), "--out", str(tmp_path / "runs")],
                       capture_output=True, text=True, env={**base_env, "FAKE_REPORT": "I did it, tests pass."})
    assert r.returncode == 1 and r.stdout.startswith("VERDICT: NONCOMPLIANT")
    r = run_cli(env, ["run-worker", "--spec", str(spec), "--budget", "-3", "--dry-run"])
    assert r.returncode == 2


# --------------------------------------------------------------------------- task brief (governor.py brief)

REPO = Path(__file__).resolve().parents[3]

GOOD_BRIEF = """# Brief: port-api-tests

## Task
Port the tests under tests/api to the savepoint fixture and keep the suite green.

## Definition of done
- [ ] `grep -rl db_session tests/api` prints nothing
- [ ] `pytest -q tests/api` exits 0
- [ ] no file outside tests/api and tests/conftest.py is modified (`git status --short`)

## Evidence
```
$ pytest -q tests/api
$ git status --short
```

## Out of scope
- the application code under test
- tests outside tests/api

## Decisions already made
- the fixture lives in tests/conftest.py — one home, no plugin import magic

## Assumptions
- the savepoint fixture already exists and is named db_savepoint

## Procedure
- run /governor:triage first and show the table before any work
- /governor:delegate per slice; workers: governor:implementer; governor:reviewer on every slice that changes behaviour
- plugin agents by name, nothing implemented inline
- a BLOCKED or PARTIAL report is answered by the conductor and re-delegated
"""


def replace_section(text, heading, body):
    """GOOD_BRIEF with one section's body swapped; body ends with a blank line."""
    start = text.index(f"## {heading}\n") + len(f"## {heading}\n")
    end = text.find("\n## ", start)
    end = len(text) if end < 0 else end + 1
    return text[:start] + body + text[end:]


def brief_problems_of(text, **cfg):
    return governor.brief_check_problems(text, {**governor.DEFAULTS, **cfg})


def test_brief_good_brief_passes_and_template_does_not():
    assert brief_problems_of(GOOD_BRIEF) == []
    problems = brief_problems_of(governor.BRIEF_TEMPLATE.read_text())
    assert problems and any("not checkable" in p for p in problems), problems


def test_brief_rule_1_headings():
    assert "missing '## Assumptions'" in brief_problems_of(GOOD_BRIEF.replace("## Assumptions", "## Guesses"))
    assert "missing '## Task'" in brief_problems_of(GOOD_BRIEF.replace("## Task", "## Tasks"))
    assert "missing '## Decisions already made'" not in brief_problems_of(GOOD_BRIEF.replace("## Decisions already made", "## Choices"))


def test_brief_rule_2_task_is_one_short_line():
    two = replace_section(GOOD_BRIEF, "Task", "Port the tests.\nAnd keep it green.\n\n")
    assert any("'## Task' must be one non-empty line, found 2" in p for p in brief_problems_of(two))
    assert any("limit 240" in p for p in brief_problems_of(replace_section(GOOD_BRIEF, "Task", "port " * 60 + "\n\n")))
    assert governor.section_body(GOOD_BRIEF, "Task").strip() == "Port the tests under tests/api to the savepoint fixture and keep the suite green."
    assert governor.section_body(GOOD_BRIEF, "Nowhere") == ""


def test_brief_rule_3_done_items_are_two_and_checkable():
    one = replace_section(GOOD_BRIEF, "Definition of done", "- [ ] `pytest -q tests/api` exits 0\n\n")
    assert any("at least 2 items, found 1" in p for p in brief_problems_of(one))
    bad = replace_section(GOOD_BRIEF, "Definition of done", "- [ ] `pytest -q tests/api` exits 0\n- [ ] the fixture is used everywhere\n\n")
    assert "definition of done item 2 is not checkable: 'the fixture is used everywhere'" in brief_problems_of(bad)
    wrapped = replace_section(GOOD_BRIEF, "Definition of done", "- [ ] `pytest -q tests/api` exits 0\n- [ ] a test that was failing before\n  is listed, not fixed\n\n")
    assert brief_problems_of(wrapped) == []
    assert governor.done_items("- [ ] a test that\n  was failing is listed\n- second\n") == ["a test that was failing is listed", "second"]
    assert governor.done_items("1. `a` exits 0\n2) [ ] b is zero\n3. c is listed\n") == ["`a` exits 0", "b is zero", "c is listed"]
    assert governor.done_items("---\n- [ ] `a` exits 0\n---\n- \n-\n* ---\n- b is zero\n") == ["`a` exits 0", "b is zero"]
    numbered = replace_section(GOOD_BRIEF, "Definition of done", "1. `pytest -q tests/api` exits 0\n2. `git status --short` lists only tests/api\n3. tests/api/conftest.py exists\n\n")
    assert brief_problems_of(numbered) == []
    ruled = replace_section(GOOD_BRIEF, "Definition of done", "---\n- [ ] `pytest -q tests/api` exits 0\n\n")
    assert any("at least 2 items, found 1" in p for p in brief_problems_of(ruled))


def test_brief_rule_4_vague_words():
    task = replace_section(GOOD_BRIEF, "Task", "Improve the api tests under tests/api.\n\n")
    assert "vague word 'improve' in '## Task': say what is observable instead" in brief_problems_of(task)
    item = replace_section(GOOD_BRIEF, "Definition of done", "- [ ] `pytest -q tests/api` exits 0, fixtures updated as needed\n- [ ] `git status --short` lists only tests/api\n\n")
    assert any(p.startswith("vague word 'as needed' in definition of done item 1") for p in brief_problems_of(item))
    assert governor.vague_words_in("improvements to the goods") == []  # whole words only


def test_brief_rule_5_evidence_command():
    prose = replace_section(GOOD_BRIEF, "Evidence", "pytest, green\n\n")
    assert "'## Evidence' needs a fenced block with the command on a '$ ' line" in brief_problems_of(prose)
    no_dollar = replace_section(GOOD_BRIEF, "Evidence", "```\npytest -q tests/api\n```\n\n")
    assert any("'$ '" in p for p in brief_problems_of(no_dollar))
    # A '$ ' fence under a later section is not evidence: the scan is bounded to the Evidence body.
    undecided = replace_section(GOOD_BRIEF, "Evidence", "to be decided\n\n")
    below = replace_section(undecided, "Procedure", "- run /governor:triage first\n```\n$ ls\n```\n")
    assert any("'## Evidence' needs" in p for p in brief_problems_of(below)), brief_problems_of(below)
    assert governor.evidence_commands(below) == ["ls"]  # the report contract still reads to the end
    assert governor.fenced_commands(governor.section_body(below, "Evidence")) == []


def test_brief_rule_6_procedure():
    assert any("must run /governor:triage" in p for p in brief_problems_of(GOOD_BRIEF.replace("/governor:triage", "/governor:delegate")))
    gp = GOOD_BRIEF.replace("plugin agents by name", "general-purpose for the porting")
    assert any("names general-purpose" in p for p in brief_problems_of(gp))
    assert brief_problems_of(GOOD_BRIEF.replace("## Out of scope\n", "## Out of scope\n- general-purpose refactors\n")) == []


def test_brief_rule_7_length():
    assert any(f"brief is {len(GOOD_BRIEF)} chars, limit 300" in p for p in brief_problems_of(GOOD_BRIEF, brief_max_chars=300))


def test_brief_checkable_words():
    two = replace_section(GOOD_BRIEF, "Definition of done", "- [ ] `pytest -q tests/api` exits 0\n- [ ] tests pass properly\n\n")
    probs = [p for p in brief_problems_of(two) if "item 2" in p]
    assert len(probs) == 2 and any("not checkable" in p for p in probs) and any("vague word 'properly'" in p for p in probs), probs
    ok = replace_section(GOOD_BRIEF, "Definition of done", "- [ ] `pytest -q tests/e2e` exits 0\n- [ ] `pytest -q tests/api` exits 0\n\n")
    assert brief_problems_of(ok) == []
    assert governor.is_checkable("the dead tests are listed in the plan")
    assert governor.is_checkable("tests/conftest.py has one fixture")
    assert governor.is_checkable("conftest.py has one fixture")
    assert governor.is_checkable("3 duplicate tests are gone")
    assert not governor.is_checkable("the suite is in a happier state")
    assert not governor.is_checkable("e.g. the fixture is shared")


def test_brief_cli(env, tmp_path):
    good = tmp_path / "brief.md"; good.write_text(GOOD_BRIEF)
    r = run_cli(env, ["brief", "check", str(good)])
    assert r.returncode == 0 and r.stdout == f"OK brief={good}\n", r.stdout
    bad = tmp_path / "bad.md"; bad.write_text(GOOD_BRIEF.replace("## Procedure", "## Steps"))
    r = run_cli(env, ["brief", "check", str(bad)])
    assert r.returncode == 1 and r.stdout.startswith("NONCOMPLIANT brief=") and "- missing '## Procedure'" in r.stdout
    r = run_cli(env, ["brief", "template"])
    assert r.returncode == 0 and r.stdout == governor.BRIEF_TEMPLATE.read_text()
    r = run_cli(env, ["brief", "check", "-"], stdin=r.stdout)
    assert r.returncode == 1 and r.stdout.startswith("NONCOMPLIANT brief=-") and "not checkable" in r.stdout
    r = run_cli(env, ["brief", "check", "-"], stdin=GOOD_BRIEF)
    assert r.returncode == 0 and r.stdout.startswith("OK brief=-")
    r = run_cli(env, ["brief", "check"], stdin=GOOD_BRIEF)
    assert r.returncode == 0
    for args in (["brief", "lint"], ["brief"]):
        r = run_cli(env, args)
        assert r.returncode == 2 and r.stdout.startswith("usage: governor.py brief check"), r.stdout
    r = run_cli(env, ["brief", "check", str(tmp_path / "missing.md")])
    assert r.returncode == 1 and r.stdout.startswith(f"NONCOMPLIANT brief={tmp_path / 'missing.md'}\n- cannot read "), r.stdout
    cp = tmp_path / "cp1252.md"; cp.write_bytes(NOT_UTF8)
    r = run_cli(env, ["brief", "check", str(cp)], extra_env=UTF8_ENV)
    assert r.returncode == 1 and r.stdout.startswith(f"NONCOMPLIANT brief={cp}\n- cannot read "), r.stdout + r.stderr


def test_brief_template_unreadable_fails_closed(env, tmp_path, monkeypatch):
    bad = tmp_path / "template.md"; bad.write_bytes(NOT_UTF8)
    monkeypatch.setattr(governor, "BRIEF_TEMPLATE", bad)
    monkeypatch.setenv("PYTHONUTF8", "1")
    import io
    out = io.StringIO(); monkeypatch.setattr(sys, "stdout", out)
    assert governor.cmd_brief(["template"], governor.DEFAULTS) == 1
    assert out.getvalue().startswith(f"cannot read {bad}: ") and out.getvalue().count("\n") == 1
    monkeypatch.setattr(governor, "BRIEF_TEMPLATE", tmp_path / "absent.md")
    out.seek(0); out.truncate()
    assert governor.cmd_brief(["template"], governor.DEFAULTS) == 1 and out.getvalue().count("\n") == 1


def test_run_exit_policy_hooks_fail_open_cli_fails_closed(env, monkeypatch, capsys):
    def boom(argv):
        raise RuntimeError("boom " + argv[0])
    monkeypatch.setattr(governor, "main", boom)
    for ev in sorted(governor.HOOK_EVENTS):
        assert governor.run([ev]) == 0
    assert capsys.readouterr().err == ""
    for argv in (["brief", "check", "x.md"], ["check-report", "r.md"], ["plan", "check", "p.json"], ["status"]):
        assert governor.run(argv) == 1
        assert capsys.readouterr().err == f"governor: RuntimeError: boom {argv[0]}\n"
    logged = (env["state"] / "errors.log").read_text()
    assert "boom pre-tool-use" in logged and "boom brief" in logged


def test_hook_events_match_hooks_json():
    hooks = json.loads((BIN.parent / "hooks" / "hooks.json").read_text())["hooks"]
    verbs = {h["command"].split("governor.py\" ")[1].split()[0] for group in hooks.values() for entry in group for h in entry["hooks"]}
    assert verbs == set(governor.HOOK_EVENTS)


def test_section_body_ignores_hashes_inside_fences():
    fenced = replace_section(GOOD_BRIEF, "Definition of done", "- [ ] `pytest -q tests/api` exits 0\n```\n# undo it\n```\n- [ ] the code is properly refactored and cleaner\n\n")
    fenced = replace_section(fenced, "Procedure", "- run /governor:triage first\n```\n# a shell comment\n```\n- delegate to general-purpose for everything\n")
    problems = brief_problems_of(fenced)
    assert [p for p in problems if "not checkable" in p] == ["definition of done item 2 is not checkable: 'the code is properly refactored and cleaner'"]
    assert sorted(p for p in problems if p.startswith("vague word")) == [
        "vague word 'cleaner' in definition of done item 2: say what is observable instead",
        "vague word 'properly' in definition of done item 2: say what is observable instead",
    ]
    assert [p for p in problems if "general-purpose" in p] == ["'## Procedure' names general-purpose: do not name it at all, even to forbid it; it is pinned to Sonnet but inherits the session's effort, so name the plugin agents instead"]
    assert len(problems) == 4, problems
    # A bare '#' line, or '#' without a space, is not a heading and does not end the section.
    assert governor.section_body("## Task\nline\n#\n#tag\n## Next\nno", "Task") == "line\n#\n#tag"
    assert governor.section_body("## Task\nline\n~~~sh\n## not a heading\n~~~\nafter\n### Sub\nno", "Task") == "line\n~~~sh\n## not a heading\n~~~\nafter"


def test_playbook_worked_brief_passes_the_lint():
    md = (REPO / "docs" / "PLAYBOOK.md").read_text()
    start = md.index("## A worked brief")
    m = re.search(r"^````\n(.*?)^````$", md[start:], re.S | re.M)
    assert m, "the worked brief must sit in a four-backtick fence so its evidence fence nests"
    assert brief_problems_of(m.group(1)) == []
