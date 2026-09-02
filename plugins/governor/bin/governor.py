#!/usr/bin/env python3
"""governor: token guardrails for Claude Code sessions that run on an expensive model.

Two callers share this file:

* Claude Code hooks. Each hook event runs ``governor.py <event>`` with the hook's
  JSON on stdin and reads the JSON this prints on stdout. Exit 0 always: a
  broken guardrail must never lock a session; errors go to the log file
  instead (see ``STATE_DIR``).
* The ``/governor:budget`` skill, which runs ``governor.py status`` and
  ``governor.py budget ...`` to show or change the session budget.

Standard library only, Python 3.9+. Every constant carries the reason for its
value. Anything that reads the transcript is incremental: the ledger stores a
byte offset per file and only parses what was appended since the last call,
because the PreToolUse hook runs before every tool call and a multi-megabyte
transcript would otherwise be re-read each time.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent

# --------------------------------------------------------------------------- config

DEFAULTS: Dict[str, Any] = {
    # Substrings that mark a model as the expensive tier. Matched against both
    # full ids ("claude-fable-5-1") and Agent-tool aliases ("fable").
    "expensive_models": ["fable", "mythos"],
    # The model a spawn gets when it names none. Sonnet is the cheapest model
    # that implements from a spec reliably; Haiku is for the scout agent only.
    "worker_model": "sonnet",
    # Rewrite model-less spawns even when the session itself runs on a cheap
    # model. True because "inherit" on an Opus conductor is still 2.5x Sonnet.
    "always_pin_workers": True,
    # Expensive-tier spend, in USD at API list price, after which tool calls
    # are denied until the session changes model or raises the budget. 15 USD
    # is roughly 250k Fable output tokens: a full design session, not a full
    # implementation loop, which is the point.
    "budget_usd": 15.0,
    # Fraction of the budget at which a warning is injected once.
    "warn_at": 0.7,
    # Fork subagents copy the whole context onto the parent model, the most
    # expensive spawn there is. Off unless someone decides otherwise.
    "allow_fork": False,
    # A spawn onto an expensive model must carry a brief with these headings.
    # A prompt that cannot fill them in is not a hard question yet.
    "brief_headings": ["Question", "Context", "Definition of done"],
    # A brief longer than this is pasting material the agent should read
    # itself; 8000 chars is about two pages.
    "brief_max_chars": 8000,
    # Expensive-model spawns allowed per session. Three consults is a lot of
    # architecture for one session; more usually means the conductor is
    # forwarding routine work.
    "max_expensive_spawns": 3,
    # agent type -> model, for agents whose definition the scanner cannot find.
    "pinned_agents": {},
    # agent type -> report contract enforced at SubagentStop.
    "report_contracts": {
        "implementer": "worker",
        "senior-implementer": "worker",
        "test-implementer": "worker",
        "scout": "scout",
        "reviewer": "reviewer",
    },
    "enforce_reports": True,
    # How many times SubagentStop may send a worker back for a missing report
    # section before accepting it as-is. Two: one honest miss, one retry.
    "max_report_blocks": 2,
}

CONFIG_FILENAME = "governor.json"
STATE_DIR_ARG: Optional[str] = None  # set from --state-dir before anything touches the state


def config_paths(project_dir: Optional[str]) -> List[Path]:
    """Lowest precedence first: user file, then project file, then $GOVERNOR_CONFIG."""
    paths = [Path.home() / ".claude" / CONFIG_FILENAME]
    if project_dir:
        paths.append(Path(project_dir) / ".claude" / CONFIG_FILENAME)
    if os.environ.get("GOVERNOR_CONFIG"):
        paths.append(Path(os.environ["GOVERNOR_CONFIG"]))
    return paths


def load_config(project_dir: Optional[str]) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULTS))
    for p in config_paths(project_dir):
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


def state_dir() -> Path:
    """Where ledgers live. In order: $GOVERNOR_STATE_DIR, the --state-dir the
    hook passed (Claude Code substitutes ${CLAUDE_PLUGIN_DATA}, which survives
    plugin updates), $CLAUDE_PLUGIN_DATA if exported, then ~/.cache/governor."""
    for d in (os.environ.get("GOVERNOR_STATE_DIR"), STATE_DIR_ARG, os.environ.get("CLAUDE_PLUGIN_DATA")):
        if d and not d.startswith("${"):
            return Path(d)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "governor"


def log_error(msg: str) -> None:
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "errors.log").open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- pricing


class Pricing:
    def __init__(self, data: Dict[str, Any]):
        self.models: Dict[str, Dict[str, float]] = data.get("models", {})
        self.aliases: Dict[str, str] = data.get("aliases", {})

    @classmethod
    def load(cls, path: Path = HERE / "pricing.json") -> "Pricing":
        return cls(json.loads(path.read_text()))

    def resolve(self, model: Optional[str]) -> Optional[str]:
        if not model:
            return None
        model = self.aliases.get(model, model)
        best = ""
        for key in self.models:
            if model.startswith(key) and len(key) > len(best):
                best = key
        return best or None

    def cost_usd(self, model: Optional[str], usage: Dict[str, Any]) -> float:
        key = self.resolve(model)
        if not key:
            return 0.0
        p = self.models[key]
        cc = usage.get("cache_creation") or {}
        w5 = cc.get("ephemeral_5m_input_tokens")
        w1h = cc.get("ephemeral_1h_input_tokens", 0)
        if w5 is None:  # older transcripts carry only the total
            w5 = usage.get("cache_creation_input_tokens", 0) - w1h
        per_m = (
            usage.get("input_tokens", 0) * p["input"]
            + usage.get("output_tokens", 0) * p["output"]
            + w5 * p["cache_write_5m"]
            + w1h * p["cache_write_1h"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
        )
        return per_m / 1_000_000


def is_expensive(model: Optional[str], cfg: Dict[str, Any]) -> bool:
    if not model:
        return False
    m = model.lower()
    return any(tok in m for tok in cfg["expensive_models"])


# --------------------------------------------------------------------------- ledger

EMPTY_TOTALS = {
    "messages": 0,
    "input": 0,
    "output": 0,
    "cache_write_5m": 0,
    "cache_write_1h": 0,
    "cache_read": 0,
    "cost_usd": 0.0,
}


class Ledger:
    """Per-session spend, built incrementally from the transcript files.

    State lives in ``<STATE_DIR>/sessions/<session_id>.json``. ``files`` maps a
    transcript path to the byte offset already parsed; ``seen`` holds message
    ids already counted, because Claude Code writes one transcript line per
    content block and repeats the message's usage on each of them.
    """

    def __init__(self, session_id: str, pricing: Pricing):
        self.session_id = session_id
        self.pricing = pricing
        self.path = state_dir() / "sessions" / f"{session_id}.json"
        self.state: Dict[str, Any] = {
            "files": {},
            "seen": [],
            "models": {},  # model id -> totals (main + subagents)
            "agents": {},  # agent id -> {"model", "effort", "cost_usd", "messages"}
            "tool_results": {},  # tool name -> bytes returned into the main context
            "main_model": None,
            "main_effort": None,
            "spawns": [],
            "expensive_spawns": 0,
            "warned": False,
            "report_blocks": {},
            "pending_tool_uses": {},
        }
        try:
            self.state.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass
        self._seen = set(self.state["seen"])

    # -- persistence
    def save(self) -> None:
        # Bound the seen-set: 4000 ids is far more than one session's messages
        # and keeps the state file small.
        self.state["seen"] = list(self._seen)[-4000:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        os.replace(tmp, self.path)

    # -- transcript ingestion
    def update(self, transcript_path: Optional[str]) -> None:
        if not transcript_path:
            return
        main = Path(transcript_path)
        self._ingest(main, agent_id=None)
        subdir = main.with_suffix("") / "subagents"
        if subdir.is_dir():
            for f in sorted(subdir.glob("agent-*.jsonl")):
                self._ingest(f, agent_id=f.stem[len("agent-"):])

    def _ingest(self, path: Path, agent_id: Optional[str]) -> None:
        key = str(path)
        offset = int(self.state["files"].get(key, 0))
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size < offset:  # truncated or rotated: start over for this file
            offset = 0
        if size == offset:
            return
        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
        # Only consume whole lines; a partial trailing line is re-read next time.
        last_nl = chunk.rfind(b"\n")
        if last_nl < 0:
            return
        consumed = chunk[: last_nl + 1]
        self.state["files"][key] = offset + len(consumed)
        for raw in consumed.split(b"\n"):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            self._ingest_line(obj, agent_id)

    def _ingest_line(self, obj: Dict[str, Any], agent_id: Optional[str]) -> None:
        typ = obj.get("type")
        msg = obj.get("message") or {}
        if typ == "assistant":
            content = msg.get("content")
            if isinstance(content, list) and agent_id is None:
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        self.state["pending_tool_uses"][block.get("id", "")] = block.get("name", "?")
            mid = msg.get("id")
            if not mid or mid in self._seen:
                return
            self._seen.add(mid)
            model = msg.get("model") or "unknown"
            usage = msg.get("usage") or {}
            cost = self.pricing.cost_usd(model, usage)
            t = self.state["models"].setdefault(model, dict(EMPTY_TOTALS))
            cc = usage.get("cache_creation") or {}
            w1h = cc.get("ephemeral_1h_input_tokens", 0)
            w5 = cc.get("ephemeral_5m_input_tokens")
            if w5 is None:
                w5 = usage.get("cache_creation_input_tokens", 0) - w1h
            t["messages"] += 1
            t["input"] += usage.get("input_tokens", 0)
            t["output"] += usage.get("output_tokens", 0)
            t["cache_write_5m"] += w5
            t["cache_write_1h"] += w1h
            t["cache_read"] += usage.get("cache_read_input_tokens", 0)
            t["cost_usd"] += cost
            if agent_id is None:
                self.state["main_model"] = model
                self.state["main_effort"] = obj.get("effort") or self.state["main_effort"]
            else:
                a = self.state["agents"].setdefault(
                    agent_id, {"model": model, "effort": None, "cost_usd": 0.0, "messages": 0}
                )
                a["model"] = model
                a["effort"] = obj.get("effort") or a["effort"]
                a["cost_usd"] += cost
                a["messages"] += 1
        elif typ == "user" and agent_id is None:
            content = msg.get("content")
            if not isinstance(content, list):
                return
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                name = self.state["pending_tool_uses"].pop(block.get("tool_use_id", ""), "?")
                body = block.get("content")
                size = len(body) if isinstance(body, str) else len(json.dumps(body)) if body else 0
                self.state["tool_results"][name] = self.state["tool_results"].get(name, 0) + size

    # -- queries
    def expensive_spend(self, cfg: Dict[str, Any]) -> float:
        return sum(t["cost_usd"] for m, t in self.state["models"].items() if is_expensive(m, cfg))

    def total_spend(self) -> float:
        return sum(t["cost_usd"] for t in self.state["models"].values())

    def main_model(self) -> Optional[str]:
        return self.state.get("main_model") or self.state.get("start_model")

    def note_hook_context(self, hook: Dict[str, Any]) -> None:
        """Record what the hook input itself says about the session (model at
        SessionStart, effort level on every event)."""
        if hook.get("model") and not hook.get("agent_id"):
            self.state["start_model"] = hook["model"]
        eff = hook.get("effort")
        if isinstance(eff, dict) and eff.get("level") and not hook.get("agent_id"):
            self.state["main_effort"] = eff["level"]

    def record_spawn(self, subagent_type: str, model: Optional[str], action: str) -> None:
        self.state["spawns"].append({"type": subagent_type, "model": model, "action": action, "ts": time.time()})

    def readout(self, cfg: Dict[str, Any]) -> str:
        """One line for the per-turn context injection."""
        exp = self.expensive_spend(cfg)
        budget = float(cfg["budget_usd"])
        exp_models = {m: t for m, t in self.state["models"].items() if is_expensive(m, cfg)}
        out_tok = sum(t["output"] for t in exp_models.values())
        cread = sum(t["cache_read"] for t in exp_models.values())
        spawns = self.state["spawns"]
        by_model: Dict[str, int] = {}
        for s in spawns:
            if s["action"] != "deny":
                by_model[s["model"] or "?"] = by_model.get(s["model"] or "?", 0) + 1
        spawn_txt = ", ".join(f"{m} {n}" for m, n in sorted(by_model.items())) or "none"
        model = self.main_model() or "unknown"
        return (
            f"[governor] expensive-tier ${exp:.2f} of ${budget:.2f} "
            f"(out {_k(out_tok)} tok, cache-read {_k(cread)}) · total ${self.total_spend():.2f} "
            f"· session model {model} · spawns: {spawn_txt}"
        )

    def report(self, cfg: Dict[str, Any]) -> str:
        """Markdown for `governor.py status`."""
        lines = [f"# governor: session {self.session_id}", ""]
        lines.append(f"Budget (expensive tier): ${float(cfg['budget_usd']):.2f}  ·  spent: ${self.expensive_spend(cfg):.2f}  ·  all models: ${self.total_spend():.2f}")
        lines.append(f"Session model: {self.main_model() or 'unknown'}  ·  effort: {self.state.get('main_effort') or 'unknown'}")
        lines += ["", "| model | messages | input | output | cache write 5m | cache write 1h | cache read | USD |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for m, t in sorted(self.state["models"].items(), key=lambda kv: -kv[1]["cost_usd"]):
            lines.append(f"| {m} | {t['messages']} | {_k(t['input'])} | {_k(t['output'])} | {_k(t['cache_write_5m'])} | {_k(t['cache_write_1h'])} | {_k(t['cache_read'])} | {t['cost_usd']:.2f} |")
        if self.state["agents"]:
            lines += ["", "| subagent | model | effort | messages | USD |", "|---|---|---|---:|---:|"]
            for aid, a in sorted(self.state["agents"].items(), key=lambda kv: -kv[1]["cost_usd"]):
                flag = " (inherited session effort?)" if a.get("effort") in ("xhigh", "max") and not is_expensive(a.get("model"), cfg) else ""
                lines.append(f"| {aid} | {a['model']} | {a.get('effort') or '?'}{flag} | {a['messages']} | {a['cost_usd']:.2f} |")
        if self.state["tool_results"]:
            lines += ["", "Tool results returned into the main context (bytes; the conductor paid to read every one):", ""]
            for name, size in sorted(self.state["tool_results"].items(), key=lambda kv: -kv[1])[:8]:
                lines.append(f"- {name}: {_k(size)}")
        if self.state["spawns"]:
            lines += ["", "Spawns:", ""]
            for s in self.state["spawns"][-12:]:
                lines.append(f"- {s['type']} → {s['model'] or '?'} ({s['action']})")
        return "\n".join(lines)


def _k(n: int) -> str:
    n = int(n)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.0f}k"
    return str(n)


# --------------------------------------------------------------------------- policy

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.S)


def agent_model_from_file(path: Path) -> Optional[str]:
    try:
        text = path.read_text()
    except OSError:
        return None
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    for line in m.group(1).splitlines():
        if line.strip().startswith("model:"):
            return line.split(":", 1)[1].strip().strip("'\"") or None
    return None


def agent_dirs(project_dir: Optional[str]) -> List[Path]:
    dirs = []
    if project_dir:
        dirs.append(Path(project_dir) / ".claude" / "agents")
    dirs.append(Path.home() / ".claude" / "agents")
    dirs.append(PLUGIN_ROOT / "agents")
    return dirs


def declared_model(subagent_type: str, cfg: Dict[str, Any], project_dir: Optional[str]) -> Optional[str]:
    """The model an agent definition pins, or None when it inherits."""
    if subagent_type in cfg["pinned_agents"]:
        return cfg["pinned_agents"][subagent_type]
    short = subagent_type.split(":", 1)[-1]
    for d in agent_dirs(project_dir):
        for name in (subagent_type, short):
            model = agent_model_from_file(d / f"{name}.md")
            if model and model != "inherit":
                return model
    # Claude Code's own resolution order puts this env var after frontmatter
    # and before the session model, so a spawn that reaches here inherits it.
    env = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")
    return env or None


def brief_problems(prompt: str, cfg: Dict[str, Any]) -> List[str]:
    problems = []
    for h in cfg["brief_headings"]:
        if not re.search(rf"^#{{1,6}}\s*{re.escape(h)}\b", prompt, re.M | re.I):
            problems.append(f"missing heading '## {h}'")
    if len(prompt) > int(cfg["brief_max_chars"]):
        problems.append(f"brief is {len(prompt)} chars, limit {cfg['brief_max_chars']}: point at files instead of pasting them")
    return problems


def agent_policy(tool_input: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger, project_dir: Optional[str]) -> Dict[str, Any]:
    """Decide what happens to an Agent tool call.

    Returns {"action": "deny"|"rewrite"|"allow", "reason": str, "model": str|None,
    "updated_input": dict|None}.
    """
    sub = str(tool_input.get("subagent_type") or "general-purpose")
    explicit = tool_input.get("model")
    prompt = str(tool_input.get("prompt") or "")
    session_model = ledger.main_model()
    session_expensive = is_expensive(session_model, cfg)

    if sub == "fork" and not cfg["allow_fork"]:
        return {
            "action": "deny",
            "model": session_model,
            "reason": (
                "governor: fork subagents copy the whole context onto the session model"
                f" ({session_model or 'unknown'}), the most expensive spawn there is."
                " Spawn a fresh agent with a written brief instead (governor:implementer,"
                " governor:scout, governor:reviewer), or set allow_fork=true in .claude/governor.json."
            ),
        }

    model = explicit if explicit and explicit != "inherit" else declared_model(sub, cfg, project_dir)

    if model is None:
        if cfg["always_pin_workers"] or session_expensive or session_model is None:
            updated = dict(tool_input)
            updated["model"] = cfg["worker_model"]
            return {
                "action": "rewrite",
                "model": cfg["worker_model"],
                "updated_input": updated,
                "reason": (
                    f"governor: '{sub}' named no model and would inherit {session_model or 'the session model'};"
                    f" pinned to {cfg['worker_model']}. Pass model explicitly to choose otherwise."
                ),
            }
        return {"action": "allow", "model": session_model, "reason": ""}

    if is_expensive(model, cfg):
        problems = brief_problems(prompt, cfg)
        if problems:
            heads = ", ".join(f"## {h}" for h in cfg["brief_headings"])
            return {
                "action": "deny",
                "model": model,
                "reason": (
                    f"governor: spawning '{sub}' on {model} needs a structured brief; "
                    + "; ".join(problems)
                    + f". Required headings: {heads}. Say what decision is needed, the files that"
                    " bound it, and what a done answer contains. If this is routine work, use"
                    f" governor:implementer ({cfg['worker_model']}) instead."
                ),
            }
        if ledger.state["expensive_spawns"] >= int(cfg["max_expensive_spawns"]):
            return {
                "action": "deny",
                "model": model,
                "reason": (
                    f"governor: {ledger.state['expensive_spawns']} expensive-model spawns already this session"
                    f" (limit {cfg['max_expensive_spawns']}). Batch the remaining questions into one brief,"
                    " or raise max_expensive_spawns in .claude/governor.json."
                ),
            }
        return {"action": "allow", "model": model, "reason": "expensive spawn with a brief"}

    return {"action": "allow", "model": model, "reason": ""}


# --------------------------------------------------------------------------- report contracts

FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


def report_problems(text: str, contract: str) -> List[str]:
    """What a worker's final message lacks under its contract. Empty list = accepted."""
    problems: List[str] = []

    def has_heading(h: str) -> bool:
        return re.search(rf"^#{{1,6}}\s*{re.escape(h)}\b", text, re.M | re.I) is not None

    if contract == "worker":
        if not has_heading("Result"):
            problems.append("missing '## Result' with one of DONE, PARTIAL, BLOCKED")
        elif not re.search(r"^#{1,6}\s*Result\b[^\n]*\n\s*(?:\*\*)?(DONE|PARTIAL|BLOCKED)\b", text, re.M | re.I) and not re.search(r"^#{1,6}\s*Result\b[^\n]*\b(DONE|PARTIAL|BLOCKED)\b", text, re.M | re.I):
            problems.append("'## Result' must state DONE, PARTIAL or BLOCKED on its first line")
        if not has_heading("Changed files"):
            problems.append("missing '## Changed files' (a list, or 'none')")
        if not has_heading("Evidence"):
            problems.append("missing '## Evidence'")
        else:
            tail = text[re.search(r"^#{1,6}\s*Evidence\b", text, re.M | re.I).end():]
            fences = FENCE_RE.findall(tail)
            if not any(re.search(r"^\$ \S", f, re.M) for f in fences):
                problems.append("'## Evidence' needs a fenced block with the command on a '$ ' line followed by its output")
    elif contract == "scout":
        if not has_heading("Findings"):
            problems.append("missing '## Findings' with path:line references")
        elif not re.search(r"\S+\.\w+:\d+", text):
            problems.append("'## Findings' must cite at least one path:line")
    elif contract == "reviewer":
        ok = False
        for f in FENCE_RE.findall(text):
            try:
                obj = json.loads(f)
            except ValueError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("findings"), list):
                ok = all(isinstance(x, dict) and x.get("failure_scenario") for x in obj["findings"])
                break
        if not ok:
            problems.append("must end with a fenced JSON block {\"findings\": [...]} where every finding has a failure_scenario")
    return problems


def last_assistant_text(transcript: Path) -> str:
    """Text of the final assistant message in a transcript (all its text blocks)."""
    last_id = None
    texts: Dict[str, List[str]] = {}
    try:
        with transcript.open() as f:
            for raw in f:
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                msg = obj.get("message") or {}
                mid = msg.get("id") or obj.get("uuid")
                content = msg.get("content")
                if isinstance(content, str):
                    texts.setdefault(mid, []).append(content)
                elif isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "text":
                            texts.setdefault(mid, []).append(b.get("text", ""))
                last_id = mid
    except OSError:
        return ""
    return "\n".join(texts.get(last_id, []))


def agent_transcript_for(hook: Dict[str, Any]) -> Optional[Path]:
    p = hook.get("agent_transcript_path")
    if p:
        return Path(p)
    aid = hook.get("agent_id")
    tp = hook.get("transcript_path")
    if aid and tp:
        cand = Path(tp).with_suffix("") / "subagents" / f"agent-{aid}.jsonl"
        if cand.exists():
            return cand
    return None


def agent_type_for(hook: Dict[str, Any], transcript: Optional[Path]) -> Optional[str]:
    t = hook.get("agent_type") or hook.get("subagent_type")
    if t:
        return str(t)
    if transcript:
        meta = transcript.with_suffix(".meta.json")
        try:
            m = json.loads(meta.read_text())
            return m.get("customAgentType") or m.get("agentType")
        except (OSError, ValueError):
            return None
    return None


# --------------------------------------------------------------------------- hook handlers


def emit(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def policy_text(ledger: Ledger, cfg: Dict[str, Any]) -> str:
    try:
        text = (PLUGIN_ROOT / "policy.md").read_text().strip()
    except OSError:
        text = "governor active."
    return text + "\n\n" + ledger.readout(cfg)


def h_session_start(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    ledger.note_hook_context(hook)
    ledger.update(hook.get("transcript_path"))
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": policy_text(ledger, cfg),
        }
    }


def h_user_prompt(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    ledger.note_hook_context(hook)
    ledger.update(hook.get("transcript_path"))
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": ledger.readout(cfg),
        }
    }


def h_pre_tool_use(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger, project_dir: Optional[str]) -> Dict[str, Any]:
    ledger.note_hook_context(hook)
    ledger.update(hook.get("transcript_path"))
    tool = hook.get("tool_name")
    tool_input = hook.get("tool_input") or {}

    # Whose tool call is this? A subagent's calls are gated on the subagent's
    # own model: a Sonnet worker keeps working while Fable is over budget.
    caller_agent = hook.get("agent_id")
    if caller_agent:
        caller_model = (ledger.state["agents"].get(str(caller_agent)) or {}).get("model")
    else:
        caller_model = ledger.main_model()

    # Budget gate first: it applies to every tool, Agent included.
    spend = ledger.expensive_spend(cfg)
    budget = float(cfg["budget_usd"])
    if is_expensive(caller_model, cfg) and budget > 0 and spend >= budget:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"governor: expensive-tier spend ${spend:.2f} has reached the session budget ${budget:.2f}."
                    " Context is preserved: switch with /model opus (or sonnet) and continue, or raise the budget"
                    " with /governor:budget set <usd>. Write down the state first if the next step is a decision."
                ),
            }
        }
    out: Dict[str, Any] = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if budget > 0 and spend >= budget * float(cfg["warn_at"]) and not ledger.state["warned"] and is_expensive(caller_model, cfg):
        ledger.state["warned"] = True
        out["systemMessage"] = f"governor: ${spend:.2f} of the ${budget:.2f} expensive-tier budget used. Delegate what remains; keep the conductor's turns short."

    if tool == "Agent":
        decision = agent_policy(tool_input, cfg, ledger, project_dir)
        ledger.record_spawn(str(tool_input.get("subagent_type") or "general-purpose"), decision.get("model"), decision["action"])
        if decision["action"] == "deny":
            out["hookSpecificOutput"]["permissionDecision"] = "deny"
            out["hookSpecificOutput"]["permissionDecisionReason"] = decision["reason"]
        elif decision["action"] == "rewrite":
            out["hookSpecificOutput"]["permissionDecision"] = "allow"
            out["hookSpecificOutput"]["updatedInput"] = decision["updated_input"]
            out["systemMessage"] = decision["reason"]
        elif decision.get("reason") == "expensive spawn with a brief":
            ledger.state["expensive_spawns"] += 1
    if len(out["hookSpecificOutput"]) == 1 and "systemMessage" not in out:
        return {}
    return out


def h_subagent_stop(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    ledger.update(hook.get("transcript_path"))
    if not cfg["enforce_reports"]:
        return {}
    transcript = agent_transcript_for(hook)
    atype = agent_type_for(hook, transcript)
    if not atype:
        return {}
    contract = cfg["report_contracts"].get(atype) or cfg["report_contracts"].get(atype.split(":", 1)[-1])
    if not contract or not (transcript or hook.get("last_assistant_message")):
        return {}
    aid = str(hook.get("agent_id") or (transcript.stem if transcript else "unknown"))
    blocks = ledger.state["report_blocks"].get(aid, 0)
    if blocks >= int(cfg["max_report_blocks"]) or (hook.get("stop_hook_active") and blocks >= 1):
        return {"systemMessage": f"governor: accepted {atype} report after {blocks} block(s) without a full contract; verify its evidence yourself."}
    text = hook.get("last_assistant_message")
    if not isinstance(text, str) or not text.strip():
        text = last_assistant_text(transcript)
    problems = report_problems(text, contract)
    if not problems:
        return {}
    ledger.state["report_blocks"][aid] = blocks + 1
    return {
        "decision": "block",
        "reason": (
            f"governor: your final report does not meet the '{contract}' contract: "
            + "; ".join(problems)
            + ". Add what is missing and finish. Do not restart the task."
        ),
    }


def h_session_end(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    ledger.update(hook.get("transcript_path"))
    try:
        hist = state_dir() / "history.jsonl"
        hist.parent.mkdir(parents=True, exist_ok=True)
        with hist.open("a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "session_id": ledger.session_id,
                "cwd": hook.get("cwd"),
                "reason": hook.get("reason"),
                "main_model": ledger.main_model(),
                "expensive_usd": round(ledger.expensive_spend(cfg), 4),
                "total_usd": round(ledger.total_spend(), 4),
                "models": ledger.state["models"],
                "spawns": len(ledger.state["spawns"]),
            }) + "\n")
    except OSError as e:
        log_error(f"history append failed: {e}")
    return {}


# --------------------------------------------------------------------------- CLI


def cmd_status(args: List[str], cfg: Dict[str, Any]) -> int:
    sid = _arg(args, "--session") or os.environ.get("CLAUDE_SESSION_ID") or _latest_session_id()
    if not sid:
        print("governor: no session ledger yet (hooks have not run in this session).")
        return 0
    ledger = Ledger(sid, Pricing.load())
    tp = _arg(args, "--transcript")
    if tp:
        ledger.update(tp)
        ledger.save()
    print(ledger.report(cfg))
    return 0


def cmd_budget(args: List[str], cfg: Dict[str, Any], project_dir: Optional[str]) -> int:
    if not args or args[0] == "show":
        print(f"budget_usd: {cfg['budget_usd']}  worker_model: {cfg['worker_model']}  allow_fork: {cfg['allow_fork']}  max_expensive_spawns: {cfg['max_expensive_spawns']}")
        print("config files (low to high precedence): " + ", ".join(str(p) for p in config_paths(project_dir)))
        return 0
    if args[0] == "set" and len(args) >= 2:
        try:
            value = float(args[1])
        except ValueError:
            print("usage: governor.py budget set <usd> [--user]")
            return 2
        target = Path.home() / ".claude" / CONFIG_FILENAME if "--user" in args else Path(project_dir or ".") / ".claude" / CONFIG_FILENAME
        data: Dict[str, Any] = {}
        try:
            data = json.loads(target.read_text())
        except (OSError, ValueError):
            pass
        data["budget_usd"] = value
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2) + "\n")
        print(f"budget_usd={value} written to {target}. Applies from the next tool call.")
        return 0
    if args[0] == "history":
        hist = state_dir() / "history.jsonl"
        try:
            rows = [json.loads(l) for l in hist.read_text().splitlines() if l.strip()]
        except OSError:
            rows = []
        if not rows:
            print("no session history yet")
            return 0
        print("| when | session | model | expensive USD | total USD | spawns |")
        print("|---|---|---|---:|---:|---:|")
        for r in rows[-20:]:
            print(f"| {r['ts']} | {r['session_id'][:8]} | {r.get('main_model')} | {r['expensive_usd']:.2f} | {r['total_usd']:.2f} | {r['spawns']} |")
        return 0
    print("usage: governor.py budget [show|set <usd> [--user]|history]")
    return 2


def _arg(args: List[str], flag: str) -> Optional[str]:
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _latest_session_id() -> Optional[str]:
    d = state_dir() / "sessions"
    try:
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return files[-1].stem if files else None


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    event, args = argv[0], argv[1:]
    global STATE_DIR_ARG
    if "--state-dir" in args:
        i = args.index("--state-dir")
        STATE_DIR_ARG = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    cfg = load_config(project_dir)
    if event == "status":
        return cmd_status(args, cfg)
    if event == "budget":
        return cmd_budget(args, cfg, project_dir)

    try:
        hook = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except ValueError:
        hook = {}
    sid = str(hook.get("session_id") or "unknown")
    ledger = Ledger(sid, Pricing.load())
    handlers = {
        "session-start": lambda: h_session_start(hook, cfg, ledger),
        "user-prompt": lambda: h_user_prompt(hook, cfg, ledger),
        "pre-tool-use": lambda: h_pre_tool_use(hook, cfg, ledger, project_dir),
        "subagent-stop": lambda: h_subagent_stop(hook, cfg, ledger),
        "session-end": lambda: h_session_end(hook, cfg, ledger),
    }
    if event not in handlers:
        print(f"governor: unknown event {event}", file=sys.stderr)
        return 2
    out = handlers[event]()
    ledger.save()
    if out:
        emit(out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as e:  # never take the session down with us
        log_error(f"{sys.argv[1:]}: {type(e).__name__}: {e}")
        sys.exit(0)
