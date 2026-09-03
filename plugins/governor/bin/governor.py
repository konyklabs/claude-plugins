#!/usr/bin/env python3
"""governor: token guardrails for Claude Code sessions that run on an expensive model.

Two callers share this file:

* Claude Code hooks. Each hook event runs ``governor.py <event>`` with the hook's
  JSON on stdin and reads the JSON this prints on stdout. For these events, exit
  0 always: a broken guardrail must never lock a session; errors go to the log
  file instead (see ``STATE_DIR`` and ``HOOK_EVENTS``).
* The skills, which run subcommands instead of reasoning a step out:
  ``status`` and ``budget`` (the ledger), ``check-report`` (a worker report
  against its contract), ``brief check`` and ``brief template`` (the task
  brief), ``plan`` (slices to levels), ``run-worker`` (a headless slice).
  These fail closed: an input the verb cannot read is a NONCOMPLIANT verdict,
  and an unexpected error exits 1 with one line on stderr.

Standard library only, Python 3.9+. Every constant carries the reason for its
value. Anything that reads the transcript is incremental: the ledger stores a
byte offset per file and only parses what was appended since the last call,
because the PreToolUse hook runs before every tool call and a multi-megabyte
transcript would otherwise be re-read each time.
"""
from __future__ import annotations

import fcntl
import json
import math
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
        "scanner": "worker",
        "auditor": "reviewer",
    },
    "enforce_reports": True,
    # How many times SubagentStop may send a worker back for a missing report
    # section before accepting it as-is. Two: one honest miss, one retry.
    "max_report_blocks": 2,
    # The budget gate itself. Off only by an explicit user decision; a budget
    # of zero means "closed", never "unlimited".
    "enforce_budget": True,
    # Namespaces whose agents are held to report_contracts by bare name. A
    # project agent that happens to be called "reviewer" is not governed:
    # plugin agents always arrive namespaced (verified: governor:scout), so
    # a bare agent type is a project or user agent, and those are governed
    # only when listed here by the user.
    "contract_namespaces": ["governor", "py-testing", "prod-readiness"],
    "govern_bare_agents": [],
    # "enforce": deny, rewrite and block as documented. "observe": keep the
    # ledger and the readout, never change or refuse anything; for measuring
    # a workflow before governing it, or for a session that must not be
    # interrupted. "explore": for a loosely defined question; rigor attaches
    # to the first push, not to the start of work, so before it a session may
    # work loosely while the protections that cost nothing stay on (workers
    # pinned, forks denied, spend tracked); report contracts are off and the
    # budget is a one-time checkpoint instead of a wall.
    "mode": "enforce",
    # "line": one spend line per turn in context. "start": only at
    # SessionStart. "off": nothing in context; use the status line or
    # /governor:budget instead.
    "readout": "line",
    # Permission decision returned with a model rewrite. "none" sends the
    # rewritten input without a decision, so the session's own permission
    # rules still apply (verified on 2.1.258: the rewrite takes effect).
    # "allow" approves the spawn as a side effect; only for harnesses where
    # the rewrite is otherwise ignored.
    "rewrite_decision": "none",
    # Headless worker runs (governor.py run-worker): the hard per-run cap and
    # the tool allowlist print mode may use without a prompt.
    "worker_budget_usd": 2.0,
    "worker_allowed_tools": [],
    "worker_timeout_s": 3600,
}

# Keys a project-level file may only tighten. A repository can make the
# session stricter for whoever opens it, never looser: loosening is the
# user's decision, made in ~/.claude/governor.json or $GOVERNOR_CONFIG.
TIGHTEN_ONLY = {
    "budget_usd": "lower",
    "warn_at": "lower",
    "max_expensive_spawns": "lower",
    "brief_max_chars": "lower",
    "allow_fork": "false",
    "enforce_reports": "true",
    "enforce_budget": "true",
    "always_pin_workers": "true",
    "mode": "enforce",
    "expensive_models": "superset",
    "brief_headings": "superset",
}
NUMERIC_KEYS = {"budget_usd": float, "warn_at": float, "max_expensive_spawns": int, "brief_max_chars": int, "max_report_blocks": int}

MODES = ("enforce", "observe", "explore")
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


def _finite_number(v: Any, kind: type) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return kind(v)


def _would_loosen(key: str, new: Any, cur: Any) -> bool:
    rule = TIGHTEN_ONLY[key]
    if rule == "lower":
        return new > cur
    if rule == "false":
        return bool(new) and not bool(cur)
    if rule == "true":
        return (not bool(new)) and bool(cur)
    if rule == "superset":
        return not set(cur) <= set(new)
    if rule == "enforce":
        return new != "enforce" and cur == "enforce"
    return False


def load_config(project_dir: Optional[str]) -> Dict[str, Any]:
    """DEFAULTS, then the user file, then the project file, then $GOVERNOR_CONFIG.

    Every value is type-checked (a bad value falls back to the previous one
    and is reported), and the project file may only tighten the guardrail
    keys in TIGHTEN_ONLY. What was ignored is listed under cfg["_ignored"] so
    the session can say so."""
    cfg = json.loads(json.dumps(DEFAULTS))
    ignored: List[str] = []
    paths = config_paths(project_dir)
    project_path = Path(project_dir) / ".claude" / CONFIG_FILENAME if project_dir else None
    user_path = paths[0]
    for p in paths:
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            if p.exists():
                ignored.append(f"{p}: unreadable ({type(e).__name__})")
            continue
        if not isinstance(data, dict):
            ignored.append(f"{p}: not a JSON object")
            continue
        is_project = project_path is not None and p == project_path
        items = list(data.items())
        if p == user_path and isinstance(data.get("projects"), dict) and project_dir:
            # "projects": {"/abs/project/dir": {...}} in the user's own file:
            # per-project settings with user authority (may raise the budget).
            per_project = data["projects"].get(str(Path(project_dir).resolve())) or data["projects"].get(str(project_dir)) or {}
            items = [(k, v) for k, v in items if k != "projects"] + list(per_project.items())
        elif "projects" in data:
            items = [(k, v) for k, v in items if k != "projects"]
            ignored.append(f"{p}: 'projects' is only honoured in the user file")
        for k, v in items:
            if k not in DEFAULTS:
                ignored.append(f"{p}: unknown key {k!r}")
                continue
            if k in NUMERIC_KEYS:
                num = _finite_number(v, NUMERIC_KEYS[k])
                if num is None:
                    ignored.append(f"{p}: {k} must be a finite number, got {v!r}")
                    continue
                v = num
            elif isinstance(DEFAULTS[k], bool):
                if not isinstance(v, bool):
                    ignored.append(f"{p}: {k} must be true or false, got {v!r}")
                    continue
            elif isinstance(DEFAULTS[k], list):
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    ignored.append(f"{p}: {k} must be a list of strings")
                    continue
            elif isinstance(DEFAULTS[k], dict):
                if not isinstance(v, dict):
                    ignored.append(f"{p}: {k} must be an object")
                    continue
            elif isinstance(DEFAULTS[k], str) and not isinstance(v, str):
                ignored.append(f"{p}: {k} must be a string")
                continue
            if k == "mode" and v not in MODES:
                ignored.append(f"{p}: mode must be one of {', '.join(MODES)}, got {v!r}")
                continue
            if is_project and k in TIGHTEN_ONLY and _would_loosen(k, v, cfg[k]):
                ignored.append(f"{p}: {k}={v!r} would loosen the user's {cfg[k]!r}; project files may only tighten")
                continue
            if isinstance(v, dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    if is_expensive(cfg["worker_model"], cfg):
        ignored.append(f"worker_model={cfg['worker_model']!r} is an expensive model; using {DEFAULTS['worker_model']!r}")
        cfg["worker_model"] = DEFAULTS["worker_model"]
    cfg["_ignored"] = ignored
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

    def fallback_key(self) -> str:
        """The dearest entry: an unknown model is priced as if it were Fable,
        so a gap in the table can only close the gate early, never leave it
        open."""
        return max(self.models, key=lambda k: self.models[k]["output"])

    def priced_key(self, model: Optional[str]) -> Tuple[str, bool]:
        key = self.resolve(model)
        return (key, True) if key else (self.fallback_key(), False)

    def cost_usd(self, model: Optional[str], usage: Dict[str, Any]) -> float:
        key, _ = self.priced_key(model)
        p = self.models[key]
        w5, w1h = split_cache_writes(usage)
        per_m = (
            usage.get("input_tokens", 0) * p["input"]
            + usage.get("output_tokens", 0) * p["output"]
            + w5 * p["cache_write_5m"]
            + w1h * p["cache_write_1h"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
        )
        return per_m / 1_000_000


def split_cache_writes(usage: Dict[str, Any]) -> Tuple[int, int]:
    """(5-minute, 1-hour) cache-write token counts. The breakdown is the
    source of truth when present; the flat total minus the 1h tier is the
    fallback, clamped so a missing total can never turn into a discount."""
    cc = usage.get("cache_creation") or {}
    w1h = int(cc.get("ephemeral_1h_input_tokens") or 0)
    w5 = cc.get("ephemeral_5m_input_tokens")
    if w5 is None:
        w5 = max(0, int(usage.get("cache_creation_input_tokens") or 0) - w1h)
    return int(w5), w1h


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
            "explore_checkpoint": False,  # the one deny explore mode issues at the budget
            "report_blocks": {},
            "pending_tool_uses": {},
            "unpriced_models": [],
            "start_model": None,
        }
        try:
            self.state.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass
        self._seen: Dict[str, None] = dict.fromkeys(self.state["seen"])

    # -- persistence
    def save(self) -> None:
        # Bound the seen-set to the most recent 4000 ids (insertion-ordered):
        # far more than one session's messages, and it keeps the file small.
        self.state["seen"] = list(self._seen)[-4000:]
        self.state["spawns"] = self.state["spawns"][-200:]
        if len(self.state["pending_tool_uses"]) > 500:
            self.state["pending_tool_uses"] = dict(list(self.state["pending_tool_uses"].items())[-500:])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A temp name unique to this process: hooks for one session run
        # concurrently (every worker's tool call is its own process), and a
        # shared temp path would let two writers interleave into one file.
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.state))
        os.replace(tmp, self.path)

    # -- transcript ingestion
    @staticmethod
    def main_transcript(transcript_path: str) -> Path:
        """The session transcript, even when a hook fired inside a subagent and
        handed us the subagent's own file (<sid>/subagents/agent-<id>.jsonl).
        Counting that file as the main thread would make the worker's model
        look like the session's and lift the budget gate."""
        p = Path(transcript_path)
        if p.parent.name == "subagents" and p.name.startswith("agent-"):
            session_dir = p.parent.parent
            return session_dir.parent / (session_dir.name + ".jsonl")
        return p

    def update(self, transcript_path: Optional[str]) -> None:
        if not transcript_path:
            return
        main = self.main_transcript(transcript_path)
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
            self._seen[mid] = None
            model = msg.get("model") or "unknown"
            usage = msg.get("usage") or {}
            cost = self.pricing.cost_usd(model, usage)
            _, known = self.pricing.priced_key(model)
            if not known and model not in self.state["unpriced_models"]:
                self.state["unpriced_models"].append(model)
            t = self.state["models"].setdefault(model, dict(EMPTY_TOTALS))
            w5, w1h = split_cache_writes(usage)
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
        self.state["spawns"].append({"type": clean_label(subagent_type), "model": clean_label(model or ""), "action": action, "ts": time.time()})

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
        line = (
            f"[governor] expensive-tier ${exp:.2f} of ${budget:.2f} "
            f"(out {_k(out_tok)} tok, cache-read {_k(cread)}) · total ${self.total_spend():.2f} "
            f"· session model {model} · spawns: {spawn_txt}"
        )
        if self.state["unpriced_models"]:
            line += " · unpriced (charged at the top rate): " + ", ".join(self.state["unpriced_models"])
        if cfg.get("_ignored"):
            line += f" · {len(cfg['_ignored'])} config value(s) ignored, see /governor:budget"
        return line

    def report(self, cfg: Dict[str, Any]) -> str:
        """Markdown for `governor.py status`."""
        lines = [f"# governor: session {self.session_id}", ""]
        lines.append(f"Budget (expensive tier): ${float(cfg['budget_usd']):.2f}  ·  spent: ${self.expensive_spend(cfg):.2f}  ·  all models: ${self.total_spend():.2f}")
        lines.append(f"Session model: {self.main_model() or 'unknown'}  ·  effort: {self.state.get('main_effort') or 'unknown'}")
        if self.state["unpriced_models"]:
            lines.append("Models missing from pricing.json, charged at the top rate: " + ", ".join(self.state["unpriced_models"]))
        for note in cfg.get("_ignored", []):
            lines.append(f"Config ignored: {note}")
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


def clean_label(s: str) -> str:
    """One line, bounded, so a model-chosen string cannot impersonate the
    governor's own output when it is rendered back."""
    return re.sub(r"[^\w:@.+/-]", "_", str(s))[:80]


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


def plugin_agent_dirs(plugin: str) -> List[Path]:
    """Where another plugin's agent files can be: the install registry Claude
    Code keeps (installed_plugins.json, keyed plugin@marketplace), a sibling
    plugin in the same checkout (plugins/<name>/agents, the --plugin-dir and
    monorepo case), and a sibling in the version cache
    (<cache>/<marketplace>/<name>/<version>/agents)."""
    dirs: List[Path] = []
    registry = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(registry.read_text())
    except (OSError, ValueError):
        data = {}
    for key, entries in (data.get("plugins") or {}).items():
        if key.split("@", 1)[0] != plugin:
            continue
        for e in entries if isinstance(entries, list) else [entries]:
            path = e.get("installPath") if isinstance(e, dict) else None
            if path:
                dirs.append(Path(path) / "agents")
    dirs.append(PLUGIN_ROOT.parent / plugin / "agents")
    try:
        dirs += sorted(PLUGIN_ROOT.parent.parent.glob(f"{plugin}/*/agents"))
    except OSError:
        pass
    return dirs


def declared_model(subagent_type: str, cfg: Dict[str, Any], project_dir: Optional[str]) -> Optional[str]:
    """The model an agent definition pins, or None when it inherits."""
    if subagent_type in cfg["pinned_agents"]:
        return cfg["pinned_agents"][subagent_type]
    short = subagent_type.split(":", 1)[-1]
    if ":" in subagent_type:
        for d in plugin_agent_dirs(subagent_type.split(":", 1)[0]):
            model = agent_model_from_file(d / f"{short}.md")
            if model and model != "inherit":
                return model
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
        if not has_heading(prompt, h):
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


def heading_re(h: str) -> "re.Pattern[str]":
    """A markdown heading of any level whose text starts with ``h``; the one
    regex every heading check in this file uses."""
    return re.compile(rf"^#{{1,6}}\s*{re.escape(h)}\b", re.M | re.I)


def has_heading(text: str, h: str) -> bool:
    return heading_re(h).search(text) is not None


# A fence opener or closer: three or more backticks or tildes, optionally
# indented (a fence inside a list item is indented).
FENCE_LINE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
# A heading needs whitespace after the hashes: a bare '#' line, or '#undo' in
# a shell snippet, is not one.
SECTION_END_RE = re.compile(r"^#{1,6}\s")


def section_body(text: str, h: str) -> str:
    """The text between the heading line and the next heading line that is
    outside a fenced block; empty when the heading is absent. A '#' inside a
    fence is a comment, not a heading, so the walk tracks fences: otherwise a
    fenced shell snippet would end the section early and hide the lines after
    it from every rule that reads this body."""
    m = heading_re(h).search(text)
    if not m:
        return ""
    lines = text[m.end():].split("\n")[1:]  # drop the rest of the heading line
    out: List[str] = []
    in_fence = False
    for line in lines:
        if FENCE_LINE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence and SECTION_END_RE.match(line):
            break
        out.append(line)
    return "\n".join(out)


def bash_commands_in(transcript: Optional[Path]) -> Optional[List[str]]:
    """Every Bash command the agent actually ran, or None when the transcript
    is not available (then only the shape of the evidence can be checked)."""
    if not transcript:
        return None
    cmds: List[str] = []
    try:
        with transcript.open() as f:
            for raw in f:
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                for b in (obj.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Bash", "PowerShell"):
                        cmd = (b.get("input") or {}).get("command")
                        if isinstance(cmd, str):
                            cmds.append(" ".join(cmd.split()))
    except OSError:
        return None
    return cmds


def fenced_commands(body: str) -> List[str]:
    """Every '$ ' line inside a fenced block of ``body``, whitespace-normalised."""
    out: List[str] = []
    for fence in FENCE_RE.findall(body):
        for line in fence.splitlines():
            if line.startswith("$ ") and line[2:].strip():
                out.append(" ".join(line[2:].split()))
    return out


def evidence_commands(text: str) -> List[str]:
    """Commands under '## Evidence' to the end of the text: a worker report
    ends with its evidence, so the whole tail is the section."""
    m = heading_re("Evidence").search(text)
    if not m:
        return []
    return fenced_commands(text[m.end():])


def report_problems(text: str, contract: str, ran: Optional[List[str]] = None) -> List[str]:
    """What a worker's final message lacks under its contract. Empty list = accepted.

    ``ran`` is the list of commands the agent's transcript shows it executed;
    when given, every ``$`` line in the evidence must correspond to one of
    them, so a report cannot show output for a command that never ran."""
    problems: List[str] = []

    if contract == "worker":
        if not has_heading(text, "Result"):
            problems.append("missing '## Result' with one of DONE, PARTIAL, BLOCKED")
        elif not re.search(r"^#{1,6}\s*Result\b[^\n]*\n\s*(?:\*\*)?(DONE|PARTIAL|BLOCKED)\b", text, re.M | re.I) and not re.search(r"^#{1,6}\s*Result\b[^\n]*\b(DONE|PARTIAL|BLOCKED)\b", text, re.M | re.I):
            problems.append("'## Result' must state DONE, PARTIAL or BLOCKED on its first line")
        if not has_heading(text, "Changed files"):
            problems.append("missing '## Changed files' (a list, or 'none')")
        if not has_heading(text, "Evidence"):
            problems.append("missing '## Evidence'")
        else:
            cmds = evidence_commands(text)
            if not cmds:
                problems.append("'## Evidence' needs a fenced block with the command on a '$ ' line followed by its output")
            elif ran is not None:
                def was_run(c: str) -> bool:
                    head = c.split("|")[0].split("&&")[0].strip()
                    return any(c in r or (head and head in r) for r in ran)
                fake = [c for c in cmds if not was_run(c)]
                if fake:
                    problems.append("evidence shows commands this session never ran: " + "; ".join(fake[:3]) + ". Run them and paste the real output")
    elif contract == "scout":
        if not has_heading(text, "Findings"):
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


# --------------------------------------------------------------------------- task brief (governor.py brief)

# The brief format has one home: the file the brief skill fills in.
BRIEF_TEMPLATE = PLUGIN_ROOT / "skills" / "brief" / "references" / "brief-template.md"
# Headings the rest of the flow reads by name. "Decisions already made" is not
# here because a fresh task can honestly have none.
BRIEF_REQUIRED_HEADINGS = ("Task", "Definition of done", "Evidence", "Out of scope", "Assumptions", "Procedure")
# A goal that needs two sentences is two tasks; 240 chars is a long sentence.
BRIEF_TASK_MAX_CHARS = 240
# One done item is the task restated; the second is the first real check.
BRIEF_MIN_DONE_ITEMS = 2
# States a script or a glance can confirm. A done item with none of these, no
# backtick, no digit and no path is an opinion, and the worker will hold a
# different one.
CHECKABLE_WORDS = ("exits 0", "green", "passes", "zero", "none", "exists", "listed", "deleted", "unchanged", "identical")
# Each of these is a judgment the worker will make differently from the author.
VAGUE_WORDS = ("better", "cleaner", "clean up", "properly", "improve", "robust", "nice", "good", "as needed", "etc", "and so on", "works well", "correctly")
# A path: a slash, or a dot followed by a short extension inside a word
# ("conftest.py", "plan.md"); "e.g." and "i.e." are excluded by the trailing dot.
PATHLIKE_RE = re.compile(r"/|\w\.[A-Za-z]{1,4}\b(?!\.)")
CHECKABLE_RE = re.compile(r"`|\d|" + "|".join(rf"\b{re.escape(w)}\b" for w in CHECKABLE_WORDS), re.I)
VAGUE_RE = re.compile("|".join(rf"\b{re.escape(w)}\b" for w in VAGUE_WORDS), re.I)
# A list item: '- ', '* ', '1. ' or '1) ', optionally followed by a checkbox.
# The marker needs whitespace after it, so '---' is a rule, not an item.
DONE_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.*)$")
# A line with no word character (a horizontal rule, an empty bullet, a bare
# fence) is layout, not a check, and is neither an item nor part of one.
LAYOUT_LINE_RE = re.compile(r"^[\W_]*$")


def done_items(body: str) -> List[str]:
    """List items of a section, each with its continuation lines joined, so
    a wrapped item is checked whole."""
    items: List[str] = []
    for line in body.splitlines():
        if LAYOUT_LINE_RE.match(line):
            continue
        m = DONE_ITEM_RE.match(line)
        if m:
            if not LAYOUT_LINE_RE.match(m.group(1)):
                items.append(m.group(1).strip())
        elif items:
            items[-1] = items[-1] + " " + line.strip()
    return items


def is_checkable(item: str) -> bool:
    return bool(CHECKABLE_RE.search(item) or PATHLIKE_RE.search(item))


def vague_words_in(text: str) -> List[str]:
    seen: List[str] = []
    for m in VAGUE_RE.finditer(text):
        w = m.group(0).lower()
        if w not in seen:
            seen.append(w)
    return seen


def brief_check_problems(text: str, cfg: Dict[str, Any]) -> List[str]:
    """What a task brief (.governor/brief.md) lacks. Empty list = it passes.

    Every rule is a state a script can confirm, so the verdict is the same
    for every reader. The lint cannot judge whether the evidence command is
    the right evidence, whether the out-of-scope list is complete, or whether
    an assumption is true; the conductor reads for that."""
    problems: List[str] = []
    # 1. The headings the rest of the flow reads by name.
    for h in BRIEF_REQUIRED_HEADINGS:
        if not has_heading(text, h):
            problems.append(f"missing '## {h}'")
    # 2. One line, one sentence: a goal that needs two is two tasks.
    task_lines = [ln.strip() for ln in section_body(text, "Task").splitlines() if ln.strip()]
    if has_heading(text, "Task"):
        if len(task_lines) != 1:
            problems.append(f"'## Task' must be one non-empty line, found {len(task_lines)}")
        elif len(task_lines[0]) > BRIEF_TASK_MAX_CHARS:
            problems.append(f"'## Task' is {len(task_lines[0])} chars, limit {BRIEF_TASK_MAX_CHARS}: one sentence, or it is two tasks")
    # 3. At least two done items (one is the task restated), each checkable.
    items = done_items(section_body(text, "Definition of done"))
    if has_heading(text, "Definition of done"):
        if len(items) < BRIEF_MIN_DONE_ITEMS:
            problems.append(f"'## Definition of done' needs at least {BRIEF_MIN_DONE_ITEMS} items, found {len(items)}: one item is the task restated")
        for n, item in enumerate(items, 1):
            if not is_checkable(item):
                problems.append(f"definition of done item {n} is not checkable: '{item[:60]}'")
    # 4. Vague words in the task or a done item: each is a judgment the worker
    #    will make differently from the author.
    places = [("'## Task'", " ".join(task_lines))] + [(f"definition of done item {n}", it) for n, it in enumerate(items, 1)]
    for where, chunk in places:
        for w in vague_words_in(chunk):
            problems.append(f"vague word '{w}' in {where}: say what is observable instead")
    # 5. The evidence block is the same contract the worker report uses, read
    #    from the Evidence section only: four sections follow it in a brief,
    #    and a '$ ' line under Procedure is not evidence.
    if has_heading(text, "Evidence") and not fenced_commands(section_body(text, "Evidence")):
        problems.append("'## Evidence' needs a fenced block with the command on a '$ ' line")
    # 6. The procedure starts with triage (the table before any work is the
    #    point of the flow) and never names general-purpose: that spawn is
    #    pinned to Sonnet but inherits the session's effort; plugin agents
    #    pin their own.
    proc = section_body(text, "Procedure")
    if has_heading(text, "Procedure"):
        if "/governor:triage" not in proc:
            problems.append("'## Procedure' must run /governor:triage: the table comes before any work")
        if "general-purpose" in proc:
            problems.append("'## Procedure' names general-purpose: do not name it at all, even to forbid it; it is pinned to Sonnet but inherits the session's effort, so name the plugin agents instead")
    # 7. Same cap as the consult brief: longer is pasting material the
    #    workers should read themselves.
    if len(text) > int(cfg["brief_max_chars"]):
        problems.append(f"brief is {len(text)} chars, limit {cfg['brief_max_chars']}: point at files instead of pasting them")
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


def contract_for(agent_type: str, cfg: Dict[str, Any]) -> Optional[str]:
    """A namespaced type is governed when its namespace is listed; a bare
    type only when the user listed it in govern_bare_agents. A fully
    qualified key in report_contracts ("other:worker") matches exactly."""
    contracts = cfg["report_contracts"]
    if ":" in agent_type:
        if agent_type in contracts:
            return contracts[agent_type]
        ns, short = agent_type.split(":", 1)
        if ns in cfg["contract_namespaces"]:
            return contracts.get(short)
        return None
    if agent_type in cfg.get("govern_bare_agents", []):
        return contracts.get(agent_type)
    return None


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


EXPLORE_TEXT = (
    "governor is in explore mode, for a loosely defined question. Workers are pinned to cheap models and\n"
    "forks are denied; report contracts are off, prose answers are fine; the budget is a checkpoint, not a\n"
    "wall: at the number, one tool call is denied with the question ship, spike or drop, then work continues\n"
    "with the user's answer. When something is worth keeping, run /governor:brief, which returns to enforce.\n"
    "Write what was learned to .governor/explore.md before the session ends."
)


def h_session_start(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    ledger.note_hook_context(hook)
    ledger.update(hook.get("transcript_path"))
    if cfg.get("readout") == "off":
        return {}
    if cfg.get("mode") == "explore":
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": EXPLORE_TEXT + "\n" + ledger.readout(cfg)}}
    if cfg.get("mode") == "observe":
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "governor is in observe mode: spend is tracked, nothing is enforced.\n" + ledger.readout(cfg)}}
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": policy_text(ledger, cfg),
        }
    }


def h_user_prompt(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    ledger.note_hook_context(hook)
    ledger.update(hook.get("transcript_path"))
    if cfg.get("readout") != "line":
        return {}
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

    decision: Optional[Dict[str, Any]] = None
    if tool == "Agent":
        decision = agent_policy(tool_input, cfg, ledger, project_dir)
        ledger.record_spawn(str(tool_input.get("subagent_type") or "general-purpose"), decision.get("model"), decision["action"] if cfg.get("mode") != "observe" else f"observed:{decision['action']}")
    if cfg.get("mode") == "observe":
        return {}

    # Budget gate: every tool call from an expensive-tier caller, except a
    # spawn that hands work to a cheap worker, which is the one action that
    # reduces spend. A budget of zero or less is a closed gate, not no gate.
    spend = ledger.expensive_spend(cfg)
    budget = float(cfg["budget_usd"])
    gated = cfg["enforce_budget"] and is_expensive(caller_model, cfg)
    cheap_delegation = decision is not None and decision["action"] in ("rewrite", "allow") and not is_expensive(decision.get("model"), cfg)
    if cfg.get("mode") == "explore" and gated and spend >= budget and not cheap_delegation:
        # Explore: the budget is a checkpoint. Deny exactly once, with the
        # question, then get out of the way; a wall blocked its own escape
        # hatch in the field, a checkpoint hands the decision to the human.
        if not ledger.state.get("explore_checkpoint"):
            ledger.state["explore_checkpoint"] = True
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"governor explore checkpoint: expensive-tier ${spend:.2f} of ${budget:.2f} reached."
                        " Stop and ask the user: ship (run /governor:brief), spike (write .governor/explore.md and stop),"
                        " or drop. Further tool calls are allowed; continue only with their answer."
                    ),
                }
            }
    elif gated and spend >= budget and not cheap_delegation:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"governor: expensive-tier spend ${spend:.2f} has reached the session budget ${budget:.2f}."
                    " Context is preserved: switch with /model opus (or sonnet) and continue, or raise the budget"
                    " with /governor:budget set <usd>. Spawning cheap workers (governor:implementer, governor:scout)"
                    " is still allowed. Write down the state first if the next step is a decision."
                ),
            }
        }
    out: Dict[str, Any] = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if gated and budget > 0 and spend >= budget * float(cfg["warn_at"]) and not ledger.state["warned"]:
        ledger.state["warned"] = True
        out["systemMessage"] = f"governor: ${spend:.2f} of the ${budget:.2f} expensive-tier budget used. Delegate what remains; keep the conductor's turns short."

    if decision is not None:
        if decision["action"] == "deny":
            out["hookSpecificOutput"]["permissionDecision"] = "deny"
            out["hookSpecificOutput"]["permissionDecisionReason"] = decision["reason"]
        elif decision["action"] == "rewrite":
            if cfg.get("rewrite_decision", "none") == "allow":
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
    if not cfg["enforce_reports"] or cfg.get("mode") in ("observe", "explore"):
        return {}
    transcript = agent_transcript_for(hook)
    atype = agent_type_for(hook, transcript)
    if not atype:
        return {}
    contract = contract_for(atype, cfg)
    if not contract or not (transcript or hook.get("last_assistant_message")):
        return {}
    aid = str(hook.get("agent_id") or (transcript.stem if transcript else "unknown"))
    blocks = ledger.state["report_blocks"].get(aid, 0)
    if blocks >= int(cfg["max_report_blocks"]) or (hook.get("stop_hook_active") and blocks >= 1):
        return {"systemMessage": f"governor: accepted {atype} report after {blocks} block(s) without a full contract; verify its evidence yourself."}
    text = hook.get("last_assistant_message")
    if not isinstance(text, str) or not text.strip():
        text = last_assistant_text(transcript)
    problems = report_problems(text, contract, bash_commands_in(transcript))
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
                "mode": cfg.get("mode"),
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


def _scope(args: List[str]) -> Optional[str]:
    return "project" if "--project" in args else "user" if "--user" in args else None


def write_setting(key: str, value: Any, project_dir: Optional[str], scope: Optional[str]) -> Path:
    """Write one config key. scope None: this project's entry under
    'projects' in the user's file (the default, and the only place a raise
    or a mode change can come from); 'user': the user's file top level;
    'project': the project's .claude/governor.json, which may only tighten."""
    if scope == "project":
        target = Path(project_dir or ".") / ".claude" / CONFIG_FILENAME
    else:
        target = Path.home() / ".claude" / CONFIG_FILENAME
    data: Dict[str, Any] = {}
    try:
        data = json.loads(target.read_text())
    except (OSError, ValueError):
        pass
    if not isinstance(data, dict):
        data = {}
    if scope in ("project", "user"):
        data[key] = value
    else:
        pkey = str(Path(project_dir or ".").resolve())
        data.setdefault("projects", {})
        if not isinstance(data["projects"], dict):
            data["projects"] = {}
        data["projects"].setdefault(pkey, {})[key] = value
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n")
    return target


def cmd_mode(args: List[str], cfg: Dict[str, Any], project_dir: Optional[str]) -> int:
    """Show or set the governor mode. Usage: governor.py mode [show|explore|enforce|observe] [--user|--project]"""
    usage = "usage: governor.py mode [show|explore|enforce|observe] [--user|--project]"
    verb = next((a for a in args if not a.startswith("--")), "show")
    if verb == "show":
        print(f"mode: {cfg['mode']}")
        print("config files (low to high precedence): " + ", ".join(str(p) for p in config_paths(project_dir)))
        for note in cfg.get("_ignored", []):
            print(f"ignored: {note}")
        return 0
    if verb not in MODES:
        print(usage)
        return 2
    scope = _scope(args)
    if scope == "project" and verb != "enforce":
        # A repository can make the session stricter for whoever opens it,
        # never looser; explore and observe are the user's own decision.
        print(f"a project file may only set mode=enforce; {verb} belongs in your own ~/.claude/{CONFIG_FILENAME} (drop --project)")
        return 2
    target = write_setting("mode", verb, project_dir, scope)
    effective = load_config(project_dir)
    if effective["mode"] != verb:
        print(f"mode={verb} written to {target}, but the effective mode is {effective['mode']}: a higher-precedence entry wins"
              " (this project's entry in your user file, or a project file; see 'mode show'). " + "; ".join(effective.get("_ignored", [])[-2:]))
        return 1
    print(f"mode={verb} written to {target}. Applies from the next hook call.")
    return 0


def cmd_budget(args: List[str], cfg: Dict[str, Any], project_dir: Optional[str]) -> int:
    if not args or args[0] == "show":
        print(f"budget_usd: {cfg['budget_usd']}  worker_model: {cfg['worker_model']}  allow_fork: {cfg['allow_fork']}  max_expensive_spawns: {cfg['max_expensive_spawns']}")
        print("config files (low to high precedence): " + ", ".join(str(p) for p in config_paths(project_dir)))
        print("project files may only tighten: " + ", ".join(sorted(TIGHTEN_ONLY)))
        for note in cfg.get("_ignored", []):
            print(f"ignored: {note}")
        return 0
    if args[0] == "set" and len(args) >= 2:
        try:
            value = float(args[1])
        except ValueError:
            value = float("nan")
        if not math.isfinite(value) or value < 0:
            print("usage: governor.py budget set <usd> [--user|--project] — a finite number, 0 or more (0 closes the gate)")
            return 2
        # Default: this project's entry in the user's own file, which is the
        # only place a raise can come from (a project file may only tighten).
        target = write_setting("budget_usd", value, project_dir, _scope(args))
        effective = load_config(project_dir)
        if float(effective["budget_usd"]) != value:
            print(f"budget_usd={value} written to {target}, but the effective budget is {effective['budget_usd']}:"
                  " another config file wins (see 'budget show'). " + "; ".join(effective.get("_ignored", [])[-2:]))
            return 1
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
    print("usage: governor.py budget [show|set <usd> [--user|--project]|history]")
    return 2


def cmd_statusline(cfg: Dict[str, Any]) -> int:
    """Status-line command: Claude Code pipes session JSON on stdin, we print
    one line. Reads the saved ledger only (the hooks keep it current), so it
    returns in milliseconds and never touches the transcript itself."""
    try:
        data = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except ValueError:
        data = {}
    sid = clean_label(str(data.get("session_id") or "")) or _latest_session_id()
    model = (data.get("model") or {}).get("display_name") or (data.get("model") or {}).get("id") or "?"
    claude_cost = (data.get("cost") or {}).get("total_cost_usd")
    ctx = (data.get("context_window") or {}).get("used_percentage")
    parts = [f"governor {model}"]
    if sid:
        led = Ledger(sid, Pricing.load())
        exp = led.expensive_spend(cfg)
        budget = float(cfg["budget_usd"])
        state = "CLOSED" if cfg["enforce_budget"] and exp >= budget and is_expensive(led.main_model(), cfg) else f"${exp:.2f}/${budget:.0f}"
        parts.append(f"fable {state}")
        parts.append(f"total ${led.total_spend():.2f}")
        n = len([x for x in led.state["spawns"] if not x["action"].startswith("deny")])
        if n:
            parts.append(f"spawns {n}")
    if isinstance(claude_cost, (int, float)):
        parts.append(f"claude ${claude_cost:.2f}")
    if isinstance(ctx, (int, float)):
        parts.append(f"ctx {int(ctx)}%")
    if cfg.get("mode") == "observe":
        parts.append("observe")
    print(" · ".join(parts))
    return 0


def cmd_statusline_snippet() -> int:
    """The settings.json fragment that installs the status line. Printed, not
    written: settings are the user's to change."""
    cmd = f'python3 "{HERE / "governor.py"}" statusline'
    print(json.dumps({"statusLine": {"type": "command", "command": cmd, "padding": 1}}, indent=2))
    print("\nMerge into ~/.claude/settings.json (or the project's .claude/settings.json). The path above is this install's;", file=sys.stderr)
    print("plugin updates move it, so re-run `governor.py statusline-snippet` after an update.", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- deterministic helpers for the skills

RESULT_RE = re.compile(r"^#{1,6}\s*Result\b[^\n]*(?:\n\s*(?:\*\*)?)?(DONE|PARTIAL|BLOCKED)\b", re.M | re.I)


def cmd_check_report(args: List[str], cfg: Dict[str, Any]) -> int:
    """Check a worker report against a contract, the same way SubagentStop does.
    Usage: governor.py check-report [FILE|-] --contract worker|scout|reviewer [--transcript AGENT.jsonl]"""
    contract = _arg(args, "--contract") or "worker"
    skip = {_arg(args, "--contract"), _arg(args, "--transcript")}
    src = next((a for a in args if not a.startswith("--") and a not in skip), "-")
    try:
        text = sys.stdin.read() if src == "-" else Path(src).read_text()
    except (OSError, ValueError) as e:  # ValueError: a decode error is not a report that passed
        print(f"NONCOMPLIANT contract={contract} result=?")
        print(f"- cannot read {src}: {e}")
        return 1
    tp = _arg(args, "--transcript")
    problems = report_problems(text, contract, bash_commands_in(Path(tp)) if tp else None)
    verdict = "OK" if not problems else "NONCOMPLIANT"
    m = RESULT_RE.search(text)
    print(f"{verdict} contract={contract} result={(m.group(1).upper() if m else '?')}")
    for pr in problems:
        print(f"- {pr}")
    return 0 if not problems else 1


def cmd_brief(args: List[str], cfg: Dict[str, Any]) -> int:
    """governor.py brief check [FILE|-]  |  governor.py brief template
    The lint the brief skill runs on .governor/brief.md, and the format it fills in."""
    if not args or args[0] not in ("check", "template"):
        print("usage: governor.py brief check [FILE|-] | brief template")
        return 2
    if args[0] == "template":
        try:
            sys.stdout.write(BRIEF_TEMPLATE.read_text())
        except (OSError, ValueError) as e:
            print(f"cannot read {BRIEF_TEMPLATE}: {e}")
            return 1
        return 0
    src = next((a for a in args[1:] if not a.startswith("--")), "-")
    try:
        text = sys.stdin.read() if src == "-" else Path(src).read_text()
    except (OSError, ValueError) as e:  # a brief the tool cannot read is not a brief it can pass
        print(f"NONCOMPLIANT brief={src}")
        print(f"- cannot read {src}: {e}")
        return 1
    problems = brief_check_problems(text, cfg)
    print(f"{'OK' if not problems else 'NONCOMPLIANT'} brief={src}")
    for pr in problems:
        print(f"- {pr}")
    return 0 if not problems else 1


def plan_levels(slices: List[Dict[str, Any]]) -> Tuple[List[List[str]], List[str]]:
    """Kahn's algorithm over slice dependencies. Returns (levels, errors):
    errors name duplicate ids, unknown deps, cycles, and two slices in one
    level that touch the same file (they would collide in parallel worktrees)."""
    errors: List[str] = []
    ids = [str(x.get("id", "")) for x in slices]
    if len(set(ids)) != len(ids) or "" in ids:
        errors.append("slice ids must be unique and non-empty")
        return [], errors
    by_id = {x["id"]: x for x in slices}
    deps = {i: [str(d) for d in (by_id[i].get("deps") or [])] for i in ids}
    for i, ds in deps.items():
        for d in ds:
            if d not in by_id:
                errors.append(f"{i}: unknown dependency {d!r}")
    if errors:
        return [], errors
    remaining = set(ids)
    done: set = set()
    levels: List[List[str]] = []
    while remaining:
        ready = sorted(i for i in remaining if all(d in done for d in deps[i]))
        if not ready:
            errors.append("dependency cycle among: " + ", ".join(sorted(remaining)))
            return levels, errors
        levels.append(ready)
        done.update(ready)
        remaining.difference_update(ready)
    for lvl in levels:
        seen: Dict[str, str] = {}
        for i in lvl:
            for f in by_id[i].get("files") or []:
                if f in seen and seen[f] != i:
                    errors.append(f"{seen[f]} and {i} both change {f} in the same level; merge them or add a dependency")
                seen[f] = i
    return levels, errors


def render_plan(name: str, slices: List[Dict[str, Any]], levels: List[List[str]]) -> str:
    by_id = {x["id"]: x for x in slices}
    lines = [f"# Plan: {name}", "", "Levels run in order; slices within a level run in parallel worktrees.", ""]
    for n, lvl in enumerate(levels):
        lines += [f"## Level {n}", "", "| slice | files | command | depends on | definition of done |", "|---|---|---|---|---|"]
        for i in lvl:
            x = by_id[i]
            lines.append(f"| {i} | {', '.join(x.get('files') or [])} | `{x.get('command', '')}` | {', '.join(x.get('deps') or []) or '-'} | {x.get('dod', '')} |")
        lines.append("")
    lines.append("Integration check per level: run every command in the level, then the full suite.")
    return "\n".join(lines) + "\n"


def cmd_plan(args: List[str]) -> int:
    """governor.py plan build SLICES.json [--name N] [--out DIR]  |  governor.py plan check PLAN.json
    SLICES.json: [{"id", "files": [...], "deps": [...], "command", "dod"}, ...]"""
    if not args or args[0] not in ("build", "check") or len(args) < 2:
        print("usage: governor.py plan build SLICES.json [--name NAME] [--out DIR] | plan check PLAN.json")
        return 2
    try:
        data = json.loads(Path(args[1]).read_text())
    except (OSError, ValueError) as e:
        print(f"cannot read {args[1]}: {e}")
        return 2
    slices = data["slices"] if isinstance(data, dict) and "slices" in data else data
    if not isinstance(slices, list) or not all(isinstance(x, dict) for x in slices):
        print("slices must be a list of objects")
        return 2
    levels, errors = plan_levels(slices)
    if errors:
        print("PLAN INVALID")
        for e in errors:
            print(f"- {e}")
        return 1
    if args[0] == "check":
        print(f"PLAN OK: {len(slices)} slices in {len(levels)} levels")
        return 0
    name = _arg(args, "--name") or (data.get("name") if isinstance(data, dict) else None) or Path(args[1]).stem
    out = Path(_arg(args, "--out") or ".governor")
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.md").write_text(render_plan(str(name), slices, levels))
    (out / "plan.json").write_text(json.dumps({"name": name, "slices": slices, "levels": levels}, indent=2) + "\n")
    print(f"PLAN OK: {len(slices)} slices in {len(levels)} levels -> {out / 'plan.md'}, {out / 'plan.json'}")
    for n, lvl in enumerate(levels):
        print(f"  level {n}: {', '.join(lvl)}")
    return 0


# Tools a headless worker may use without a prompt. Print mode cannot answer a
# permission prompt, so anything outside this list is denied; widen it per
# project with worker_allowed_tools in governor.json.
DEFAULT_WORKER_TOOLS = [
    "Read", "Edit", "Write", "Grep", "Glob",
    "Bash(pytest *)", "Bash(python *)", "Bash(python3 *)", "Bash(uv run *)", "Bash(uv sync*)",
    "Bash(npm test*)", "Bash(npx *)", "Bash(ls *)", "Bash(cat *)", "Bash(git diff*)", "Bash(git status*)", "Bash(git log*)",
]


# Attempts per slice before it is FAILED. The CLI already retries retryable
# API errors inside one run; this is the outer retry for a process that
# still died on overload, which happened four times in one afternoon.
LEVEL_RETRIES = 2
# Workers per level at once. Each is a whole Claude Code process; more than
# a few saturate the API and the machine.
LEVEL_PARALLEL = max(1, min(4, (os.cpu_count() or 2) - 1))
# Seconds before the first retry, doubled each time: overloads clear in tens
# of seconds, and a worker that dies instantly must not spin.
LEVEL_BACKOFF_S = 15.0
# What a worker's death looks like when the cause is the API, not the work:
# the CLI's own error categories and the usual HTTP words. Anything else is
# not retried, because a retry would spend the budget on the same failure.
TRANSIENT_RE = re.compile(r"overloaded|rate.?limit|\b529\b|\b503\b|\b502\b|server_error|connection (?:reset|error)|ECONNRESET", re.I)
VERDICTS = ("DONE", "PARTIAL", "BLOCKED", "NONCOMPLIANT", "FAILED")


def parse_worker_output(stdout: str) -> Tuple[str, Dict[str, Any]]:
    """`claude -p --output-format json` prints one object with the text under
    'result'; anything else (older CLI, a fake in tests) is taken as the text."""
    try:
        obj = json.loads(stdout)
    except ValueError:
        return stdout, {}
    if isinstance(obj, dict) and "result" in obj:
        return str(obj.get("result") or ""), obj
    return stdout, {}


def run_worker_once(spec_path: str, agent: str, budget: str, out_dir: Path, cfg: Dict[str, Any],
                    cwd: Optional[str] = None, timeout: Optional[float] = None, resume: Optional[str] = None,
                    slug: Optional[str] = None) -> Dict[str, Any]:
    """One headless worker run. Returns verdict (one of VERDICTS), problems,
    report path, cost, session_id, transient (the failure was the API, a
    retry may help) and error. The conductor never sees the worker's tool
    output: only the verdict line and the report file."""
    out: Dict[str, Any] = {"verdict": "FAILED", "problems": [], "report": None, "cost": 0.0, "session_id": None, "transient": False, "error": ""}
    try:
        spec = Path(spec_path).read_text()
    except (OSError, ValueError) as e:
        out["error"] = f"cannot read spec: {e}"
        return out
    short = agent.split(":", 1)[-1]
    contract = cfg["report_contracts"].get(short, "worker")
    tools = cfg.get("worker_allowed_tools") or DEFAULT_WORKER_TOOLS
    slug = clean_label(slug or Path(spec_path).stem)
    report_path = out_dir / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.md"
    prompt = (
        f"You are running headlessly as {agent}. The spec is below; it is the boundary. "
        f"Do the work, run the tests it names, and end with the report format your definition requires "
        f"(## Result with DONE, PARTIAL or BLOCKED; ## Changed files; ## Evidence with each command on a '$ ' line and its output).\n\n"
        f"Spec file: {spec_path}\n\n{spec}"
    )
    if resume:
        prompt = "Continue the slice you were working on; the spec is repeated below. Finish it and report.\n\n" + prompt
    cmd = ["claude", "-p", "--agent", agent, "--max-budget-usd", str(budget), "--permission-mode", "acceptEdits",
           "--allowedTools", *tools, "--plugin-dir", str(PLUGIN_ROOT), "--output-format", "json"]
    if resume:
        cmd += ["--resume", resume]
    out["cmd"] = cmd
    import subprocess  # local import: the hooks never spawn processes
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd,
                              timeout=float(timeout if timeout is not None else cfg.get("worker_timeout_s", 3600)), check=False)
    except FileNotFoundError:
        out["error"] = "claude is not on PATH"
        return out
    except subprocess.TimeoutExpired:
        out["error"] = "worker timed out"
        return out
    text, meta = parse_worker_output(proc.stdout)
    out["cost"] = float(meta.get("total_cost_usd") or 0.0)
    out["session_id"] = meta.get("session_id")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text + (f"\n\n<!-- stderr -->\n{proc.stderr[-4000:]}" if proc.stderr.strip() else ""))
    out["report"] = str(report_path)
    died = (proc.returncode != 0 and not text.strip()) or bool(meta.get("is_error"))
    if died:
        tail = (proc.stderr.strip() or text.strip())[-300:]
        out["error"] = f"claude exited {proc.returncode}: {tail}"
        out["transient"] = bool(TRANSIENT_RE.search(proc.stderr + " " + text))
        return out
    problems = report_problems(text, contract)
    m = RESULT_RE.search(text)
    result = m.group(1).upper() if m else "?"
    out["problems"] = problems
    out["verdict"] = "NONCOMPLIANT" if problems else (result if result in VERDICTS else "NONCOMPLIANT")
    if result == "?" and not problems:
        out["problems"] = ["no '## Result' line"]
        out["verdict"] = "NONCOMPLIANT"
    return out


def cmd_run_worker(args: List[str], cfg: Dict[str, Any], project_dir: Optional[str]) -> int:
    """Run one slice headlessly under a hard dollar cap and check its report.
    Usage: governor.py run-worker --spec PATH [--agent governor:implementer] [--budget 2] [--out DIR] [--resume SESSION] [--dry-run]"""
    spec_path = _arg(args, "--spec")
    if not spec_path:
        print("usage: governor.py run-worker --spec PATH [--agent NAME] [--budget USD] [--out DIR] [--resume SESSION] [--dry-run]")
        return 2
    agent = _arg(args, "--agent") or "governor:implementer"
    budget = _arg(args, "--budget") or str(cfg.get("worker_budget_usd", 2.0))
    try:
        if not math.isfinite(float(budget)) or float(budget) <= 0:
            raise ValueError
    except ValueError:
        print("--budget must be a positive number")
        return 2
    out_dir = Path(_arg(args, "--out") or (Path(project_dir or ".") / ".governor" / "runs"))
    if "--dry-run" in args:
        tools = cfg.get("worker_allowed_tools") or DEFAULT_WORKER_TOOLS
        cmd = ["claude", "-p", "--agent", agent, "--max-budget-usd", str(budget), "--permission-mode", "acceptEdits",
               "--allowedTools", *tools, "--plugin-dir", str(PLUGIN_ROOT), "--output-format", "json"]
        print("DRY-RUN " + " ".join(cmd))
        print(f"report -> {out_dir / (clean_label(Path(spec_path).stem) + '-<timestamp>.md')}")
        return 0
    r = run_worker_once(spec_path, agent, budget, out_dir, cfg, resume=_arg(args, "--resume"))
    if r["verdict"] == "FAILED" and not r["report"]:
        print(f"ERROR {r['error']}")
        return 2 if "spec" in r["error"] or "PATH" in r["error"] else 1
    print(f"VERDICT: {r['verdict']} agent={agent} budget=${budget} cost=${r['cost']:.2f} report={r['report']}"
          + (f" session={r['session_id']}" if r.get("session_id") else ""))
    if r["error"]:
        print(f"- {r['error']}" + (" (transient)" if r["transient"] else ""))
    for pr in r["problems"]:
        print(f"- {pr}")
    return 0 if r["verdict"] == "DONE" else 1


# --------------------------------------------------------------------------- supervised levels (governor.py run-level)


def _git(root: Path, *args: str) -> Tuple[int, str]:
    import subprocess
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    return p.returncode, (p.stdout + p.stderr).strip()


def ensure_worktree(root: Path, plan: str, slice_id: str) -> Tuple[Optional[Path], str]:
    """A worktree per slice under .governor/wt/<slice> on branch <plan>/<slice>,
    reused when it already exists. Parallel workers must not share a checkout."""
    path = root / ".governor" / "wt" / clean_label(slice_id)
    branch = f"{clean_label(plan)}/{clean_label(slice_id)}"
    if path.exists():
        return path, branch
    rc, _ = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if rc == 0:
        rc, msg = _git(root, "worktree", "add", str(path), branch)
    else:
        rc, msg = _git(root, "worktree", "add", "-b", branch, str(path), "HEAD")
    if rc != 0:
        return None, msg
    return path, branch


def load_index(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text())
        if isinstance(obj, dict) and isinstance(obj.get("slices"), dict):
            return obj
    except (OSError, ValueError):
        pass
    return {}


def cmd_run_level(args: List[str], cfg: Dict[str, Any], project_dir: Optional[str]) -> int:
    """Run every slice of one plan level as a supervised headless worker.
    Usage: governor.py run-level PLAN.json --level N [--parallel K] [--budget USD] [--retries N] [--backoff S]
           [--timeout S] [--agent NAME] [--specs DIR] [--no-worktree] [--dry-run]
    Each slice: its own worktree, a bounded number of attempts with backoff on a transient
    failure, one VERDICT line. The index under .governor/runs/<plan>/ makes a rerun resume."""
    usage = "usage: governor.py run-level PLAN.json --level N [--parallel K] [--budget USD] [--retries N] [--backoff S] [--timeout S] [--agent NAME] [--specs DIR] [--no-worktree] [--dry-run]"
    plan_path = next((a for a in args if not a.startswith("--") and a.endswith(".json")), None)
    level_s = _arg(args, "--level")
    if not plan_path or level_s is None:
        print(usage)
        return 2
    try:
        plan = json.loads(Path(plan_path).read_text())
        level = int(level_s)
        levels = plan["levels"]
        slices = {str(x["id"]): x for x in plan["slices"]}
        ids = [str(i) for i in levels[level]]
    except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
        print(f"cannot read plan level: {type(e).__name__}: {e}")
        return 2
    root = Path(project_dir or ".").resolve()
    name = clean_label(str(plan.get("name") or Path(plan_path).stem))
    specs_dir = Path(_arg(args, "--specs") or (root / ".governor" / "specs"))
    out_dir = root / ".governor" / "runs" / name
    index_path = out_dir / f"level-{level}.json"
    try:
        parallel = max(1, int(_arg(args, "--parallel") or LEVEL_PARALLEL))
        retries = max(0, int(_arg(args, "--retries") or LEVEL_RETRIES))
        backoff = float(_arg(args, "--backoff") if _arg(args, "--backoff") is not None else LEVEL_BACKOFF_S)
        timeout = float(_arg(args, "--timeout") or cfg.get("worker_timeout_s", 3600))
        budget = _arg(args, "--budget") or str(cfg.get("worker_budget_usd", 2.0))
        if not math.isfinite(float(budget)) or float(budget) <= 0:
            raise ValueError("budget")
    except ValueError as e:
        print(f"bad number for {e}" if str(e) == "budget" else f"bad number: {e}")
        return 2
    agent_default = _arg(args, "--agent") or "governor:implementer"
    use_worktree = "--no-worktree" not in args

    index = load_index(index_path) or {"plan": name, "level": level, "slices": {}}
    for sid in ids:
        index["slices"].setdefault(sid, {"state": "pending", "attempts": 0, "verdict": None, "cost": 0.0, "report": None, "session_id": None, "worktree": None, "branch": None, "error": ""})
    todo = [sid for sid in ids if index["slices"][sid].get("verdict") != "DONE"]
    skipped = [sid for sid in ids if sid not in todo]

    if "--dry-run" in args:
        for sid in ids:
            spec = specs_dir / f"{sid}.md"
            print(f"{'SKIP' if sid in skipped else 'RUN '} slice={sid} spec={spec}{'' if spec.exists() else ' (missing)'} agent={slices[sid].get('agent') or agent_default}"
                  + (f" worktree={root / '.governor' / 'wt' / clean_label(sid)}" if use_worktree else ""))
        print(f"parallel={parallel} retries={retries} budget=${budget} index={index_path}")
        return 0

    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()

    def save() -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = index_path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(index, indent=2) + "\n")
        os.replace(tmp, index_path)

    def run_slice(sid: str) -> str:
        entry = index["slices"][sid]
        spec = specs_dir / f"{sid}.md"
        agent = str(slices[sid].get("agent") or agent_default)
        if not spec.exists():
            with lock:
                entry.update(state="failed", verdict="FAILED", error=f"no spec at {spec}")
                save()
                print(f"VERDICT: FAILED slice={sid} attempts=0 cost=$0.00 error=no spec at {spec}")
            return "FAILED"
        cwd: Optional[str] = None
        if use_worktree:
            with lock:
                wt, branch_or_msg = ensure_worktree(root, name, sid)
            if wt is None:
                with lock:
                    entry.update(state="failed", verdict="FAILED", error=f"worktree: {branch_or_msg[-200:]}")
                    save()
                    print(f"VERDICT: FAILED slice={sid} attempts=0 cost=$0.00 error=worktree: {branch_or_msg[-120:]}")
                return "FAILED"
            cwd = str(wt)
            with lock:
                entry.update(worktree=str(wt), branch=branch_or_msg)
        attempt = int(entry.get("attempts") or 0)
        result: Dict[str, Any] = {"verdict": "FAILED", "error": "not run", "transient": False, "cost": 0.0, "report": None, "session_id": None, "problems": []}
        while True:
            attempt += 1
            with lock:
                entry.update(state="running", attempts=attempt)
                save()
            result = run_worker_once(str(spec), agent, budget, out_dir, cfg, cwd=cwd, timeout=timeout, slug=sid)
            with lock:
                entry["cost"] = round(float(entry.get("cost") or 0.0) + float(result.get("cost") or 0.0), 4)
                entry.update(report=result.get("report") or entry.get("report"), session_id=result.get("session_id") or entry.get("session_id"),
                             error=result.get("error") or "")
                save()
            if result["verdict"] == "FAILED" and result.get("transient") and attempt <= retries:
                with lock:
                    print(f"RETRY slice={sid} attempt={attempt} in {backoff * (2 ** (attempt - 1)):.0f}s: {result['error'][-120:]}")
                time.sleep(backoff * (2 ** (attempt - 1)))
                continue
            break
        with lock:
            entry.update(state="done" if result["verdict"] == "DONE" else "failed", verdict=result["verdict"])
            save()
            line = f"VERDICT: {result['verdict']} slice={sid} attempts={attempt} cost=${entry['cost']:.2f}"
            if result.get("report"):
                line += f" report={result['report']}"
            if cwd:
                line += f" worktree={cwd}"
            if result.get("error"):
                line += f" error={result['error'][-120:]}"
            print(line)
            for pr in result.get("problems") or []:
                print(f"- {pr}")
        return str(result["verdict"])

    for sid in skipped:
        print(f"SKIP slice={sid} already DONE (index {index_path})")
    save()
    verdicts: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        for sid, v in zip(todo, pool.map(run_slice, todo)):
            verdicts[sid] = v
    counts = {v: 0 for v in VERDICTS}
    for sid in ids:
        counts[index["slices"][sid].get("verdict") or "FAILED"] = counts.get(index["slices"][sid].get("verdict") or "FAILED", 0) + 1
    print(f"LEVEL {level}: " + ", ".join(f"{counts[v]} {v}" for v in VERDICTS if counts[v]) + f" (of {len(ids)}) index={index_path}")
    return 0 if counts["DONE"] == len(ids) else 1


def cmd_runs(args: List[str], project_dir: Optional[str]) -> int:
    """Print the run index for a plan: governor.py runs [PLAN.json|NAME]"""
    root = Path(project_dir or ".").resolve()
    target = next((a for a in args if not a.startswith("--")), None)
    if target and target.endswith(".json"):
        try:
            name = clean_label(str(json.loads(Path(target).read_text()).get("name") or Path(target).stem))
        except (OSError, ValueError, AttributeError):
            print(f"cannot read {target}")
            return 2
    else:
        name = clean_label(target) if target else None
    base = root / ".governor" / "runs"
    dirs = [base / name] if name else sorted(d for d in base.glob("*") if d.is_dir()) if base.exists() else []
    rows = []
    for d in dirs:
        for f in sorted(d.glob("level-*.json")):
            idx = load_index(f)
            for sid, e in (idx.get("slices") or {}).items():
                rows.append((d.name, str(idx.get("level")), sid, str(e.get("state")), str(e.get("verdict")), str(e.get("attempts")), f"{float(e.get('cost') or 0):.2f}", str(e.get("report") or ""), (e.get("error") or "")[-60:]))
    if not rows:
        print("no runs yet" + (f" for {name}" if name else ""))
        return 0
    print("| plan | level | slice | state | verdict | attempts | USD | report | error |")
    print("|---|---:|---|---|---|---:|---:|---|---|")
    for r in rows:
        print("| " + " | ".join(r) + " |")
    return 0


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


def session_lock(sid: str):
    """An exclusive lock for the session's ledger, held from load to save so
    concurrent hook processes (one per tool call, across workers) do not lose
    each other's increments. Bounded wait: a hook has a 10 s timeout, and a
    lock held longer than 3 s means something is wrong, so we go on without
    the lock and skip the write rather than stall the session."""
    try:
        d = state_dir() / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        f = (d / f"{sid}.lock").open("a+")
    except OSError:
        return None
    deadline = time.monotonic() + 3.0
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.monotonic() > deadline:
                f.close()
                return None
            time.sleep(0.02)


def debug_dump(event: str, hook: Dict[str, Any]) -> None:
    """Shape only: field names, and sizes for the payloads. Never the
    contents of tool inputs, prompts or messages, which can carry secrets."""
    def shape(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: shape(x) for k, x in v.items()}
        if isinstance(v, list):
            return f"list[{len(v)}]"
        if isinstance(v, str):
            return f"str[{len(v)}]"
        return v
    redacted = {k: (shape(v) if k in ("tool_input", "last_assistant_message", "prompt", "tool_response") else v) for k, v in hook.items()}
    for k in ("last_assistant_message", "prompt"):
        if isinstance(hook.get(k), str):
            redacted[k] = f"str[{len(hook[k])}]"
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "hook-inputs.jsonl").open("a") as f:
            f.write(json.dumps({"event": event, "hook": redacted}) + "\n")
    except OSError:
        pass


# The five hook events, and only these, get "exit 0 whatever happens": Claude
# Code treats a non-zero hook exit as a failure it surfaces, and a broken
# guardrail must never lock a session. Every other verb is a CLI that a skill
# or a person is reading, where a silent exit 0 would pass what was not
# checked; those exit 1 with the error on stderr (see ``run``).
HOOK_EVENTS = frozenset({"session-start", "user-prompt", "pre-tool-use", "subagent-stop", "session-end"})


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
    if event == "mode":
        return cmd_mode(args, cfg, project_dir)
    if event == "statusline":
        return cmd_statusline(cfg)
    if event == "statusline-snippet":
        return cmd_statusline_snippet()
    if event == "check-report":
        return cmd_check_report(args, cfg)
    if event == "brief":
        return cmd_brief(args, cfg)
    if event == "plan":
        return cmd_plan(args)
    if event == "run-worker":
        return cmd_run_worker(args, cfg, project_dir)
    if event == "run-level":
        return cmd_run_level(args, cfg, project_dir)
    if event == "runs":
        return cmd_runs(args, project_dir)
    if event not in HOOK_EVENTS:
        print(f"governor: unknown event {event}", file=sys.stderr)
        return 2

    try:
        hook = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except ValueError:
        hook = {}
    sid = clean_label(str(hook.get("session_id") or "unknown"))
    if os.environ.get("GOVERNOR_DEBUG"):
        debug_dump(event, hook)
    lock = session_lock(sid)
    ledger = Ledger(sid, Pricing.load())
    handlers = {
        "session-start": lambda: h_session_start(hook, cfg, ledger),
        "user-prompt": lambda: h_user_prompt(hook, cfg, ledger),
        "pre-tool-use": lambda: h_pre_tool_use(hook, cfg, ledger, project_dir),
        "subagent-stop": lambda: h_subagent_stop(hook, cfg, ledger),
        "session-end": lambda: h_session_end(hook, cfg, ledger),
    }
    out = handlers[event]()
    # The decision goes out before the state is written: a full disk must not
    # turn a computed deny into silence, which Claude Code reads as consent.
    if out:
        emit(out)
    try:
        if lock is not None:
            ledger.save()
        else:
            log_error(f"{event}: session lock not acquired; state not written")
    except OSError as e:
        log_error(f"{event}: state write failed: {e}")
    finally:
        if lock is not None:
            lock.close()
    return 0


def run(argv: List[str]) -> int:
    """``main`` plus the exception policy of ``HOOK_EVENTS``: a hook event
    never takes the session down with it, a CLI verb never pretends."""
    try:
        return main(argv)
    except Exception as e:
        log_error(f"{argv}: {type(e).__name__}: {e}")
        if argv and argv[0] in HOOK_EVENTS:
            return 0
        print(f"governor: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
