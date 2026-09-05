#!/usr/bin/env python3
"""supervisor: token guardrails for Claude Code sessions that run on an expensive model.

Two callers share this file:

* Claude Code hooks. Each hook event runs ``supervisor.py <event>`` with the hook's
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
import hashlib
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
    # Route a bare "general-purpose" spawn to supervisor:worker instead of only
    # pinning its model. The Agent tool has no effort field to rewrite, so a
    # model-only pin still inherits the session's effort (field-verified: 61
    # Sonnet workers ran at xhigh, $162 at list price, before this existed).
    # True because supervisor:worker is a safe default for a caller who named
    # no agent at all: every tool, no report contract to get stuck on.
    "route_general_purpose": True,
    # Expensive-tier spend, in USD at API list price, after which tool calls
    # are denied until the session changes model or raises the budget. 15 USD
    # is roughly 250k Fable output tokens: a full design session, not a full
    # implementation loop, which is the point.
    "budget_usd": 15.0,
    # Named budgets so a task picks a size, not a number. USD at API list price.
    "budget_profiles": {"small": 5.0, "medium": 25.0, "large": 100.0},
    # Hard cap on budget_usd from any source. None means no cap. A user-level
    # setting: a project file may only lower it.
    "budget_ceiling_usd": None,
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
    # plugin agents always arrive namespaced (verified: supervisor:scout), so
    # a bare agent type is a project or user agent, and those are governed
    # only when listed here by the user.
    "contract_namespaces": ["supervisor", "py-testing", "prod-readiness"],
    "govern_bare_agents": [],
    # "off": installed but not armed; the plugin pins nothing, denies nothing,
    # injects nothing. The user arms a session with /supervisor:start,
    # /supervisor:on or /supervisor:explore; a project may set mode: enforce in
    # its own .claude/supervisor.json for a repo that is always on. "enforce":
    # deny, rewrite and block as documented. "observe": keep the ledger and
    # the readout, never change or refuse anything; for measuring a workflow
    # before governing it, or for a session that must not be interrupted.
    # "explore": for a loosely defined question; rigor attaches to the first
    # push, not to the start of work, so before it a session may work loosely
    # while the protections that cost nothing stay on (workers pinned, forks
    # denied, spend tracked); report contracts are off and the budget is a
    # one-time checkpoint instead of a wall.
    "mode": "off",
    # "line": one spend line per turn in context. "start": only at
    # SessionStart. "off": nothing in context; use the status line or
    # /supervisor:budget instead.
    "readout": "line",
    # Permission decision returned with a model rewrite. "none" sends the
    # rewritten input without a decision, so the session's own permission
    # rules still apply (verified on 2.1.258: the rewrite takes effect).
    # "allow" approves the spawn as a side effect; only for harnesses where
    # the rewrite is otherwise ignored.
    "rewrite_decision": "none",
    # Headless worker runs (supervisor.py run-worker): the hard per-run cap and
    # the tool allowlist print mode may use without a prompt.
    "worker_budget_usd": 2.0,
    "worker_allowed_tools": [],
    "worker_timeout_s": 3600,
}

# Keys a project-level file may only tighten. A repository can make the
# session stricter for whoever opens it, never looser: loosening is the
# user's decision, made in ~/.claude/supervisor.json or $SUPERVISOR_CONFIG.
TIGHTEN_ONLY = {
    "budget_usd": "lower",
    "budget_profiles": "profiles-lower",
    "budget_ceiling_usd": "ceiling-lower",
    "warn_at": "lower",
    "max_expensive_spawns": "lower",
    "brief_max_chars": "lower",
    "allow_fork": "false",
    "enforce_reports": "true",
    "enforce_budget": "true",
    "always_pin_workers": "true",
    "route_general_purpose": "true",
    "mode": "enforce",
    "expensive_models": "superset",
    "brief_headings": "superset",
}
NUMERIC_KEYS = {"budget_usd": float, "warn_at": float, "max_expensive_spawns": int, "brief_max_chars": int, "max_report_blocks": int}

MODES = ("off", "enforce", "observe", "explore")
CONFIG_FILENAME = "supervisor.json"
# The name the plugin had before 2.0. Read when the new file is absent, and
# renamed in place the first time a setting is written.
LEGACY_CONFIG_FILENAME = "governor.json"
# The plugin's own namespace, used to build every slash command and marker
# string below: a rename is one line.
NS = "supervisor"
STATE_DIR_ARG: Optional[str] = None  # set from --state-dir before anything touches the state


def _config_file(directory: Path) -> Path:
    """The config file in a directory: the current name, or the pre-2.0 name
    when only that one exists."""
    current = directory / CONFIG_FILENAME
    legacy = directory / LEGACY_CONFIG_FILENAME
    if not current.exists() and legacy.exists():
        return legacy
    return current


def config_paths(project_dir: Optional[str]) -> List[Path]:
    """Lowest precedence first: user file, then project file, then $SUPERVISOR_CONFIG."""
    paths = [_config_file(Path.home() / ".claude")]
    if project_dir:
        paths.append(_config_file(Path(project_dir) / ".claude"))
    extra = os.environ.get("SUPERVISOR_CONFIG") or os.environ.get("GOVERNOR_CONFIG")
    if extra:
        paths.append(Path(extra))
    return paths


def _finite_number(v: Any, kind: type) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return kind(v)


def _would_loosen(key: str, new: Any, cur: Any) -> bool:
    """Whether a project's value for ``key`` would loosen the user's. For
    ``budget_profiles``: a project may lower an existing profile; adding a
    name or raising a value is ignored with a note. Dropping a profile is
    not a thing a project file can do either way, because dict keys merge
    over the defaults (``cfg[k].update(v)``) rather than replacing them."""
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
        # A project may only make a session stricter: enforce is the one
        # value it may set. With off as the default, "not looser than cur"
        # would let a repository pick observe or explore.
        return new != "enforce"
    if rule == "profiles-lower":
        # A project may lower an existing profile; adding a name or raising a
        # value is loosening and is ignored with a note. Dropping a profile
        # is not a thing a project file can do: dict keys merge over the
        # defaults, they are never replaced wholesale.
        return any(name not in cur or val > cur[name] for name, val in new.items())
    if rule == "ceiling-lower":
        if cur is None:
            return False
        if new is None:
            return True
        return new > cur
    return False


def load_config(project_dir: Optional[str]) -> Dict[str, Any]:
    """DEFAULTS, then the user file, then the project file, then $SUPERVISOR_CONFIG.

    Every value is type-checked (a bad value falls back to the previous one
    and is reported), and the project file may only tighten the guardrail
    keys in TIGHTEN_ONLY. What was ignored is listed under cfg["_ignored"] so
    the session can say so."""
    cfg = json.loads(json.dumps(DEFAULTS))
    ignored: List[str] = []
    paths = config_paths(project_dir)
    project_path = _config_file(Path(project_dir) / ".claude") if project_dir else None
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
            if k == "budget_ceiling_usd":
                if v is not None:
                    num = _finite_number(v, float)
                    if num is None or num < 0:
                        ignored.append(f"{p}: budget_ceiling_usd must be null or a finite number >= 0, got {v!r}")
                        continue
                    v = num
            elif k == "budget_profiles":
                if not isinstance(v, dict):
                    ignored.append(f"{p}: budget_profiles must be an object")
                    continue
                cleaned: Dict[str, float] = {}
                for name, val in v.items():
                    num = _finite_number(val, float)
                    # A repo-controlled name is bounded before it reaches a note: a
                    # 200-char key in someone's .claude/supervisor.json must not blow
                    # up the readout it gets pasted into.
                    tname = name[:40] if isinstance(name, str) else name
                    if not isinstance(name, str) or num is None or num < 0:
                        ignored.append(f"{p}: budget_profiles.{tname!r}={val!r} must be a finite number >= 0")
                        continue
                    cleaned[name] = num
                v = cleaned
            elif k in NUMERIC_KEYS:
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
    if os.environ.get("GOVERNOR_CONFIG") and not os.environ.get("SUPERVISOR_CONFIG"):
        ignored.append("GOVERNOR_CONFIG: read under its pre-2.0 name; export SUPERVISOR_CONFIG instead")
    for p in paths:
        if p.name == LEGACY_CONFIG_FILENAME:
            ignored.append(f"{p}: read under its pre-2.0 name; rename it to {CONFIG_FILENAME} (the next write does it)")
    cfg["_ignored"] = ignored
    return cfg


def effective_budget(cfg: Dict[str, Any]) -> float:
    """budget_usd, capped by budget_ceiling_usd when one is set."""
    budget = float(cfg["budget_usd"])
    ceiling = cfg.get("budget_ceiling_usd")
    if ceiling is not None:
        return min(budget, float(ceiling))
    return budget


def profile_name(cfg: Dict[str, Any]) -> Optional[str]:
    """The profile whose value equals the effective budget, else None."""
    eb = round(effective_budget(cfg), 2)
    for name, val in (cfg.get("budget_profiles") or {}).items():
        if round(float(val), 2) == eb:
            return name
    return None


def next_profile(cfg: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    """The lowest-valued profile strictly above the effective budget, capped
    at the ceiling when one is set. None when there is no such profile."""
    eb = effective_budget(cfg)
    ceiling = cfg.get("budget_ceiling_usd")
    candidates = [
        (name, float(val)) for name, val in (cfg.get("budget_profiles") or {}).items()
        if float(val) > eb and (ceiling is None or float(val) <= ceiling)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda nv: nv[1])


def raise_advice(cfg: Dict[str, Any]) -> str:
    """The clause telling a denied session how to raise its budget, chosen by
    which raise is actually available: a named profile above the budget (and
    under the ceiling, when one is set), a numeric raise up to the ceiling, a
    ceiling raise when the budget is already pinned to it, or a bare numeric
    raise when nothing bounds it at all. Every branch ends the same way so a
    caller can always find "/model opus" in the reason."""
    tail = " /model opus keeps the context."
    nxt = next_profile(cfg)
    if nxt is not None:
        name, value = nxt
        return f"step up with /supervisor:budget set {name} (${value:.2f})." + tail
    ceiling = cfg.get("budget_ceiling_usd")
    if ceiling is not None:
        ceiling = float(ceiling)
        if round(effective_budget(cfg), 2) >= round(ceiling, 2):
            return f"the budget is at its ceiling ${ceiling:.2f}; raise it with /supervisor:budget ceiling <usd>." + tail
        return (
            f"no profile fits under the ceiling ${ceiling:.2f}; set a number up to it with"
            " /supervisor:budget set <usd>, or raise the ceiling with /supervisor:budget ceiling <usd>." + tail
        )
    return "no profile is above this budget; set a number with /supervisor:budget set <usd>." + tail


def state_dir() -> Path:
    """Where ledgers live. In order: $SUPERVISOR_STATE_DIR, the --state-dir the
    hook passed (Claude Code substitutes ${CLAUDE_PLUGIN_DATA}, which survives
    plugin updates), $CLAUDE_PLUGIN_DATA if exported, then ~/.cache/supervisor."""
    for d in (os.environ.get("SUPERVISOR_STATE_DIR"), os.environ.get("GOVERNOR_STATE_DIR"), STATE_DIR_ARG, os.environ.get("CLAUDE_PLUGIN_DATA")):
        if d and not d.startswith("${"):
            return Path(d)
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "supervisor"


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

# A subagent's row. "ended" is its fate as its transcript last showed it:
# "working" until a line says otherwise, "completed" on a final text block,
# "died" on an API error line, with "death_kind" saying which kind. Dead
# workers are 4% of the spawns on this machine and the money spent before
# they died is not visible anywhere else.
EMPTY_AGENT = {
    "model": "",
    "effort": None,
    "cost_usd": 0.0,
    "messages": 0,
    "ended": "working",
    "error": "",
    "death_kind": None,
}


def message_text(content: Any) -> str:
    """The text of an assistant message. Claude Code writes ``content`` as a
    string on some lines and as a list of blocks on others; both are read."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def response_text(resp: Any) -> str:
    """The text of a tool response as the model saw it: a string, a list of
    content blocks, or a dict with a text or content field. Anything else is
    serialised, so an unknown shape is inspected rather than trusted."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        return " ".join(response_text(b) for b in resp)
    if isinstance(resp, dict):
        if resp.get("type") == "text" or "text" in resp:
            return str(resp.get("text") or "")
        if "content" in resp:
            return response_text(resp.get("content"))
        try:
            return json.dumps(resp, default=str)
        except (TypeError, ValueError):
            return str(resp)
    return str(resp)


def death_kind(text: str) -> str:
    """Why a worker died, from the error text. Quota is tested first and is
    not a transient: a usage limit does not clear in a minute, and the field
    data is six workers that died retrying on the model that was out."""
    if QUOTA_RE.search(text):
        return "quota"
    if TRANSIENT_RE.search(text):
        return "transient"
    return "other"


def agent_ended(a: Dict[str, Any]) -> str:
    """The ``ended`` cell of the subagent table: a death carries its kind."""
    ended = a.get("ended") or "working"
    if ended == "died":
        return "died: " + str(a.get("death_kind") or "other")
    return str(ended)


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
            "agents": {},  # agent id -> EMPTY_AGENT's keys
            "deaths": [],  # spawns the Agent tool reported as dead, newest last
            "quota_hit": {},  # model id -> {"error", "ts"}: its usage limit is hit
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
            "armed": False,  # this session was armed by /supervisor:start, :on or :explore
            "armed_mode": "enforce",  # the mode armed_mode carries, "enforce" or "explore"
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
        # Deaths are rare and each one is read by a person; 50 is more than a
        # session produces and keeps the state file small either way.
        self.state["deaths"] = self.state["deaths"][-50:]
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
            model = msg.get("model") or "unknown"
            # Fate is read from every line, not only the first of a message:
            # Claude Code writes one content block per line (verified in a real
            # transcript, 2026-09-04), so the last line holds the last block,
            # while the cost below is counted once per message id.
            if agent_id is not None:
                self._note_agent_line(agent_id, model, obj, content)
            elif obj.get("isApiErrorMessage"):
                head = one_line(message_text(content), 120)
                if death_kind(head) == "quota":
                    self.note_quota_hit(self.main_model(), head)
            mid = msg.get("id")
            if not mid or mid in self._seen:
                return
            self._seen[mid] = None
            if obj.get("isApiErrorMessage"):
                # An error line carries no usage and the model "<synthetic>"
                # (verified in a real transcript, 2026-09-04): its fate was
                # noted above, and counting it would list a tier nobody ran as
                # "unpriced, charged at the top rate".
                return
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
            # Error lines returned above, so this model is a real one: the
            # last real model is the session's or the worker's model.
            if agent_id is None:
                self.state["main_model"] = model
                self.state["main_effort"] = obj.get("effort") or self.state["main_effort"]
            else:
                a = self.state["agents"].setdefault(agent_id, dict(EMPTY_AGENT, model=model))
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

    def _note_agent_line(self, agent_id: str, model: str, obj: Dict[str, Any], content: Any) -> None:
        """What this line says about the worker's fate, last line winning: an
        API error line means it died, otherwise a final text block means it
        finished and a tool_use means it is still working."""
        synthetic = bool(obj.get("isApiErrorMessage"))
        # A worker whose first line is the error has no known model; "" is
        # honest and note_quota_hit ignores it, where "<synthetic>" would be
        # priced at the top rate and shown as a tier nobody ran.
        a = self.state["agents"].setdefault(agent_id, dict(EMPTY_AGENT, model="" if synthetic else model))
        for k, v in EMPTY_AGENT.items():
            a.setdefault(k, v)  # a ledger written before deaths were recorded
        if synthetic:
            head = one_line(message_text(content), 120)
            a["ended"] = "died"
            a["error"] = head
            a["death_kind"] = death_kind(head)
            if a["death_kind"] == "quota":
                self.note_quota_hit(a.get("model"), head)
            return
        if isinstance(content, str):
            if content.strip():
                a["ended"] = "completed"
            return
        last = content[-1] if isinstance(content, list) and content else None
        if isinstance(last, dict):
            if last.get("type") == "text" and str(last.get("text") or "").strip():
                a["ended"] = "completed"
            elif last.get("type") == "tool_use":
                a["ended"] = "working"

    def note_quota_hit(self, model: Optional[str], head: str) -> None:
        """Remember, for the rest of the session, that this model's usage
        limit is hit. Only a model the pricing table resolves: an unresolvable
        id (an error line's "<synthetic>", say) is priced at the top rate by
        ``priced_key``, so recording it would deny every later spawn on the
        dearest tier over a model nobody named."""
        if not model or not self.pricing.resolve(model):
            return
        self.state["quota_hit"][model] = {"error": one_line(head, 120), "ts": time.time()}

    # -- queries
    def dead_agents(self) -> List[Tuple[str, Dict[str, Any]]]:
        """(id, row) for every subagent whose transcript ended in an API
        error, newest state, in the order the ledger recorded them."""
        return [(aid, a) for aid, a in self.state["agents"].items() if a.get("ended") == "died"]

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

    def record_spawn(self, subagent_type: str, model: Optional[str], action: str, routed_to: Optional[str] = None) -> None:
        entry = {"type": clean_label(subagent_type), "model": clean_label(model or ""), "action": action, "ts": time.time()}
        if routed_to:
            entry["routed_to"] = clean_label(routed_to)
        self.state["spawns"].append(entry)

    def readout(self, cfg: Dict[str, Any], project_dir: Optional[str] = None) -> str:
        """One line for the per-turn context injection."""
        exp = self.expensive_spend(cfg)
        budget = effective_budget(cfg)
        profile = profile_name(cfg)
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
            f"[supervisor] expensive-tier ${exp:.2f} of ${budget:.2f}"
            f"{f' ({profile})' if profile is not None else ''} "
            f"(out {_k(out_tok)} tok, cache-read {_k(cread)}) · total ${self.total_spend():.2f} "
            f"· session model {model} · spawns: {spawn_txt}"
        )
        workers = worker_spend(project_dir)
        if workers >= 0.005:  # headless workers are their own sessions; the ledger never sees them
            line += f" · workers ${workers:.2f} (all runs in this project)"
        # Transcript deaths and hook-reported deaths have no join key, so the
        # larger count stands: one death seen both ways counts once.
        dead = max(len(self.dead_agents()), len(self.state.get("deaths") or []))
        if dead:  # silent when none: a zero here would be noise on every turn
            line += f" · dead workers: {dead}"
        if self.state["unpriced_models"]:
            line += " · unpriced (charged at the top rate): " + ", ".join(self.state["unpriced_models"])
        if cfg.get("_ignored"):
            line += f" · {len(cfg['_ignored'])} config value(s) ignored, see /supervisor:budget"
        return line

    def report(self, cfg: Dict[str, Any], project_dir: Optional[str] = None) -> str:
        """Markdown for `supervisor.py status`."""
        lines = [f"# supervisor: session {self.session_id}", ""]
        lines.append(f"Budget (expensive tier): ${effective_budget(cfg):.2f}  ·  spent: ${self.expensive_spend(cfg):.2f}  ·  all models: ${self.total_spend():.2f}")
        workers = worker_spend(project_dir)
        if workers >= 0.005:
            lines.append(f"Headless workers (run-worker / run-level, every run recorded under .supervisor/runs, all sessions): ${workers:.2f}, not in the figures above")
        lines.append(f"Session model: {self.main_model() or 'unknown'}  ·  effort: {self.state.get('main_effort') or 'unknown'}")
        if self.state["unpriced_models"]:
            lines.append("Models missing from pricing.json, charged at the top rate: " + ", ".join(self.state["unpriced_models"]))
        for note in cfg.get("_ignored", []):
            lines.append(f"Config ignored: {note}")
        lines += ["", "| model | messages | input | output | cache write 5m | cache write 1h | cache read | USD |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for m, t in sorted(self.state["models"].items(), key=lambda kv: -kv[1]["cost_usd"]):
            lines.append(f"| {m} | {t['messages']} | {_k(t['input'])} | {_k(t['output'])} | {_k(t['cache_write_5m'])} | {_k(t['cache_write_1h'])} | {_k(t['cache_read'])} | {t['cost_usd']:.2f} |")
        if self.state["agents"]:
            lines += ["", "| subagent | model | effort | ended | messages | USD |", "|---|---|---|---|---:|---:|"]
            for aid, a in sorted(self.state["agents"].items(), key=lambda kv: -kv[1]["cost_usd"]):
                flag = " (inherited session effort?)" if a.get("effort") in ("xhigh", "max") and not is_expensive(a.get("model"), cfg) else ""
                lines.append(f"| {aid} | {a['model']} | {a.get('effort') or '?'}{flag} | {agent_ended(a)} | {a['messages']} | {a['cost_usd']:.2f} |")
            dead = self.dead_agents()
            if dead:
                spent = sum(a["cost_usd"] for _, a in dead)
                lines += ["", f"Dead workers: {len(dead)} (${spent:.2f} spent before death)", ""]
                for aid, a in dead:
                    lines.append(
                        f"- {aid} ({a['model'] or '?'}) died: {a.get('death_kind') or 'other'}: "
                        + one_line(a.get("error") or "", 60)
                    )
        if self.state.get("deaths"):
            # The Agent tool's own report of a death, which exists even when the
            # worker died before writing a transcript line the table could show.
            lines += ["", "Deaths reported by the Agent tool (spawn, model, kind):", ""]
            for d in self.state["deaths"][-12:]:
                lines.append(f"- {d.get('type')} ({d.get('model') or '?'}) {d.get('kind')}: " + one_line(d.get("error") or "", 60))
        if self.state["tool_results"]:
            lines += ["", "Tool results returned into the main context (bytes; the conductor paid to read every one):", ""]
            for name, size in sorted(self.state["tool_results"].items(), key=lambda kv: -kv[1])[:8]:
                lines.append(f"- {name}: {_k(size)}")
        if self.state["spawns"]:
            lines += ["", "Spawns:", ""]
            for s in self.state["spawns"][-12:]:
                dest = s.get("routed_to") or s["model"] or "?"
                lines.append(f"- {s['type']} → {dest} ({s['action']})")
        return "\n".join(lines)


def worker_spend(project_dir: Optional[str]) -> float:
    """Dollars spent by headless workers, summed from the run indexes under
    the project's .supervisor/runs. Those workers are their own sessions, so
    the conductor's ledger never sees them; this is how the readout does."""
    total = 0.0
    try:
        root = Path(str(project_dir) if project_dir else (os.environ.get("CLAUDE_PROJECT_DIR") or "."))
        for f in (root / ".supervisor" / "runs").glob("*/level-*.json"):
            # Read on every prompt: only regular files of a sane size, never through a symlink.
            if f.is_symlink() or not f.is_file() or f.stat().st_size > 1_000_000:
                continue
            try:
                idx = json.loads(f.read_text())
                slices = idx.get("slices") or {}
            except (OSError, ValueError, AttributeError):
                continue
            for e in (slices.values() if isinstance(slices, dict) else []):
                try:
                    c = float((e or {}).get("cost") or 0.0)  # one bad value loses one slice, not the file
                except (ValueError, TypeError, AttributeError):
                    continue
                if math.isfinite(c) and c >= 0:
                    total += c
    except (OSError, TypeError, ValueError):
        pass
    return round(total, 4)


def clean_label(s: str) -> str:
    """One line, bounded, so a model-chosen string cannot impersonate the
    supervisor's own output when it is rendered back."""
    return re.sub(r"[^\w:@.+/-]", "_", str(s))[:80]


# What a slice id or plan name may be: one path component, no leading dot,
# so path_label is the identity on it and two distinct ids can never share
# a worktree or a branch.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")  # \Z, not $: $ would accept a trailing newline


def one_line(s: Any, n: int = 200) -> str:
    """Untrusted text (a worker's output, a subprocess's stderr) rendered on
    the supervisor's own lines: whitespace collapsed to one line, bounded, so
    it can neither forge a VERDICT line nor break a table row."""
    return re.sub(r"\s+", " ", str(s)).strip()[:n]


def path_label(s: str) -> str:
    """A single path component from a model-chosen id: no separators, no
    leading dot, never empty. clean_label keeps '/' for display; a path must not."""
    return (re.sub(r"[^\w.-]", "_", str(s)).lstrip(".")[:80]) or "x"


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
    # A namespaced type names one plugin's agent and nothing else: a bare
    # "worker.md" in the project, the user's directory or this plugin's own
    # must not answer for "other:worker". Before this guard the supervisor's
    # own worker.md answered for any unknown plugin's "worker", and the spawn
    # went through unpinned (found in the review of roadmap#115).
    names = (subagent_type,) if ":" in subagent_type else (subagent_type, short)
    for d in agent_dirs(project_dir):
        for name in names:
            path = d / f"{name}.md"
            model = agent_model_from_file(path)
            if model and model != "inherit":
                return model
            if path.exists() and d != PLUGIN_ROOT / "agents":
                # The project's or the user's file is the agent that will run.
                # It pins nothing, so it inherits: stop here rather than let a
                # same-named plugin file answer for it (review of roadmap#115).
                return None
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


def quota_denial(model: Optional[str], ledger: Ledger, cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A deny for a spawn onto a model whose usage limit this session already
    hit, or None. Families are compared through the pricing table, so the
    alias a spawn uses ("opus") matches the id a transcript carries
    ("claude-opus-5"); an unknown id normalises to the top-rate family, which
    denies rather than lets through."""
    hits = ledger.state.get("quota_hit") or {}
    if not model or not hits:
        return None
    key, known = ledger.pricing.priced_key(model)
    for hit_model, info in hits.items():
        if ledger.pricing.priced_key(hit_model)[0] != key:
            continue
        head = one_line((info or {}).get("error") or "", 60)
        note = "" if known else f" ({model} is not in pricing.json and is treated as the {key} family; add it there to spawn it)"
        return {
            "action": "deny",
            "model": model,
            "reason": (
                f"supervisor: the {model} usage limit was hit this session ({head});"
                " a spawn onto it would die the same way. Use supervisor:implementer (sonnet)"
                " or supervisor:senior-implementer (opus), or wait for the reset." + note
            ),
        }
    return None


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
                "supervisor: fork subagents copy the whole context onto the session model"
                f" ({session_model or 'unknown'}), the most expensive spawn there is."
                " Spawn a fresh agent with a written brief instead (supervisor:implementer,"
                " supervisor:scout, supervisor:reviewer), or set allow_fork=true in .claude/supervisor.json."
            ),
        }

    model = explicit if explicit and explicit != "inherit" else declared_model(sub, cfg, project_dir)

    if model is None:
        if cfg["always_pin_workers"] or session_expensive or session_model is None:
            denial = quota_denial(cfg["worker_model"], ledger, cfg)
            if denial is not None:
                return denial
            updated = dict(tool_input)
            updated["model"] = cfg["worker_model"]
            # A bare "general-purpose" spawn has nowhere to put an effort
            # level: the Agent tool has no effort field, so a model-only pin
            # still inherits the session's effort. Routing it to
            # supervisor:worker fixes effort too, not just model.
            route = sub == "general-purpose" and cfg["route_general_purpose"]
            if route:
                updated["subagent_type"] = "supervisor:worker"
                result: Dict[str, Any] = {
                    "action": "rewrite",
                    "model": cfg["worker_model"],
                    "updated_input": updated,
                    "routed_to": "supervisor:worker",
                    "reason": (
                        f"supervisor: '{sub}' named no model and would inherit {session_model or 'the session model'}"
                        f" and its effort; routed to supervisor:worker ({cfg['worker_model']}, medium effort)."
                        " Name a supervisor agent to choose otherwise."
                    ),
                }
                return result
            return {
                "action": "rewrite",
                "model": cfg["worker_model"],
                "updated_input": updated,
                "reason": (
                    f"supervisor: '{sub}' named no model and would inherit {session_model or 'the session model'};"
                    f" pinned to {cfg['worker_model']}. Pass model explicitly to choose otherwise."
                ),
            }
        denial = quota_denial(session_model, ledger, cfg)
        if denial is not None:
            return denial
        return {"action": "allow", "model": session_model, "reason": ""}

    denial = quota_denial(model, ledger, cfg)
    if denial is not None:
        return denial

    if is_expensive(model, cfg):
        problems = brief_problems(prompt, cfg)
        if problems:
            heads = ", ".join(f"## {h}" for h in cfg["brief_headings"])
            return {
                "action": "deny",
                "model": model,
                "reason": (
                    f"supervisor: spawning '{sub}' on {model} needs a structured brief; "
                    + "; ".join(problems)
                    + f". Required headings: {heads}. Say what decision is needed, the files that"
                    " bound it, and what a done answer contains. If this is routine work, use"
                    f" supervisor:implementer ({cfg['worker_model']}) instead."
                ),
            }
        if ledger.state["expensive_spawns"] >= int(cfg["max_expensive_spawns"]):
            return {
                "action": "deny",
                "model": model,
                "reason": (
                    f"supervisor: {ledger.state['expensive_spawns']} expensive-model spawns already this session"
                    f" (limit {cfg['max_expensive_spawns']}). Batch the remaining questions into one brief,"
                    " or raise max_expensive_spawns in .claude/supervisor.json."
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


# --------------------------------------------------------------------------- task brief (supervisor.py brief)

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
    """What a task brief (.supervisor/brief.md) lacks. Empty list = it passes.

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
        if "/supervisor:triage" not in proc:
            problems.append("'## Procedure' must run /supervisor:triage: the table comes before any work")
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


def policy_text(ledger: Ledger, cfg: Dict[str, Any], project_dir: Optional[str] = None) -> str:
    try:
        text = (PLUGIN_ROOT / "policy.md").read_text().strip()
    except OSError:
        text = "supervisor active."
    return text + "\n\n" + ledger.readout(cfg, project_dir)


EXPLORE_TEXT = (
    "supervisor is in explore mode, for a loosely defined question. Workers are pinned to cheap models and\n"
    "forks are denied; report contracts are off, prose answers are fine; the budget is a checkpoint, not a\n"
    "wall: at the number, one tool call is denied with the question ship, spike or drop, then work continues\n"
    "with the user's answer. When something is worth keeping, run /supervisor:start, which returns to enforce.\n"
    "Write what was learned to .supervisor/explore.md before the session ends."
)


def effective_mode(cfg: Dict[str, Any], ledger: Ledger) -> str:
    """The mode actually in force this turn. A project or user config that
    sets anything but "off" always wins — that is an operator's decision to
    run always-on, and a session cannot un-arm it. Otherwise the session's
    own arm flag decides: armed_mode when a slash command or a skill's marker
    armed it this session, else "off" — installed but dormant."""
    if cfg.get("mode") != "off":
        return cfg["mode"]
    if ledger.state.get("armed"):
        return ledger.state.get("armed_mode") or "enforce"
    return "off"


def h_session_start(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    ledger.note_hook_context(hook)
    ledger.update(hook.get("transcript_path"))
    if cfg.get("readout") == "off":
        return {}
    mode = effective_mode(cfg, ledger)
    if mode == "off":
        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    f"{NS} is installed and dormant: nothing is pinned, denied or injected."
                    f" /{NS}:start <task> arms it for this session (/{NS}:on without a brief,"
                    f" /{NS}:explore for a question); /{NS}:off disarms."
                ),
            }
        }
    if mode == "explore":
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": EXPLORE_TEXT + "\n" + ledger.readout(cfg, hook.get("cwd"))}}
    if mode == "observe":
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "supervisor is in observe mode: spend is tracked, nothing is enforced.\n" + ledger.readout(cfg, hook.get("cwd"))}}
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": policy_text(ledger, cfg, hook.get("cwd")),
        }
    }


def _ns_command(stripped: str) -> Optional[str]:
    """The plugin command a prompt starts with (`/supervisor:on`, `/supervisor:on args`),
    or None. Exact: `/supervisor:onward` is not `on`."""
    m = re.match(rf"^/{re.escape(NS)}:([a-z-]+)(?:\s|$)", stripped)
    return m.group(1) if m else None


def h_user_prompt(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    """Arming and disarming live here: a slash command a user typed, or a
    skill's own marker line expanded into the prompt, are the only ways this
    session's dormant flag ever changes — never a tool call, never the model
    reasoning about it."""
    ledger.note_hook_context(hook)
    ledger.update(hook.get("transcript_path"))
    prompt = str(hook.get("prompt") or "")
    stripped = prompt.strip()
    arm_mode: Optional[str] = None
    cmd = _ns_command(stripped)
    if cmd in ("start", "brief", "on"):
        arm_mode = "enforce"
    elif cmd == "explore":
        arm_mode = "explore"
    elif f"<!-- {NS}:arm enforce -->" in prompt:
        arm_mode = "enforce"
    elif f"<!-- {NS}:arm explore -->" in prompt:
        arm_mode = "explore"

    # Arming when the config mode is already enforce is a no-op: the session
    # was never dormant, so there is nothing to flip and no banner to add.
    if arm_mode is not None and cfg.get("mode") not in ("off", "enforce", None):
        # observe or explore in config: the config wins over the session, so
        # an arm would inject a policy the hooks never apply. Say so instead.
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": f"{NS}: config mode is {cfg.get('mode')}, which the session cannot override; arming did nothing."
                                     f" Change it with {NS}.py mode enforce (or off) and arm again.",
            }
        }
    if arm_mode is not None and not (cfg.get("mode") == "enforce" and arm_mode == "enforce"):
        ledger.state["armed"] = True
        ledger.state["armed_mode"] = arm_mode
        ledger.save()
        body = (EXPLORE_TEXT + "\n" + ledger.readout(cfg, hook.get("cwd"))) if arm_mode == "explore" else policy_text(ledger, cfg, hook.get("cwd"))
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": f"{NS} armed for this session ({arm_mode}).\n" + body,
            }
        }

    # Disarm only by the typed command: a marker can be quoted in a pasted
    # document, and disarming is the direction that must not happen by accident.
    if arm_mode is None and cmd == "off":
        ledger.state["armed"] = False
        ledger.save()
        if cfg.get("mode") != "off":
            text = f"{NS} project config keeps it on ({cfg['mode']}); see .claude/{CONFIG_FILENAME}."
        else:
            text = f"{NS} disarmed for this session: nothing is pinned, denied or injected; spend is still tracked."
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}

    if effective_mode(cfg, ledger) == "off":
        return {}
    if cfg.get("readout") != "line":
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": ledger.readout(cfg, hook.get("cwd")),
        }
    }


DORMANT_SKILLS = ("on", "start", "explore", "brief", "off")


def h_pre_tool_use(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger, project_dir: Optional[str]) -> Dict[str, Any]:
    ledger.note_hook_context(hook)
    ledger.update(hook.get("transcript_path"))
    tool = hook.get("tool_name")
    tool_input = hook.get("tool_input") or {}
    mode = effective_mode(cfg, ledger)

    if mode == "off":
        # Dormant: the only thing this hook still does is refuse the model
        # arming itself. A spawn is recorded (dormant:<action>) so the ledger
        # and the readout stay honest, but nothing about it is changed.
        skill = ""
        if tool == "Skill":
            skill = str(tool_input.get("skill") or "")
        elif tool == "SlashCommand":
            skill = str(tool_input.get("command") or "").strip().lstrip("/").split(" ", 1)[0]
        if skill:
            if skill.startswith(f"{NS}:") and skill.split(":", 1)[1] not in DORMANT_SKILLS:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"{NS} is dormant; its skills run only after the user arms it with"
                            f" /{NS}:start <task> or /{NS}:on. Ask, do not arm it yourself."
                        ),
                    }
                }
        if tool == "Agent":
            decision = agent_policy(tool_input, cfg, ledger, project_dir)
            ledger.record_spawn(
                str(tool_input.get("subagent_type") or "general-purpose"),
                decision.get("model"),
                f"dormant:{decision['action']}",
                routed_to=decision.get("routed_to"),
            )
        return {}

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
        ledger.record_spawn(
            str(tool_input.get("subagent_type") or "general-purpose"),
            decision.get("model"),
            decision["action"] if mode != "observe" else f"observed:{decision['action']}",
            routed_to=decision.get("routed_to"),
        )
    if mode == "observe":
        return {}

    # Budget gate: every tool call from an expensive-tier caller, except a
    # spawn that hands work to a cheap worker, which is the one action that
    # reduces spend. A budget of zero or less is a closed gate, not no gate.
    spend = ledger.expensive_spend(cfg)
    budget = effective_budget(cfg)
    gated = cfg["enforce_budget"] and is_expensive(caller_model, cfg)
    cheap_delegation = decision is not None and decision["action"] in ("rewrite", "allow") and not is_expensive(decision.get("model"), cfg)
    if mode == "explore" and gated and spend >= budget and not cheap_delegation:
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
                        f"supervisor explore checkpoint: expensive-tier ${spend:.2f} of ${budget:.2f} reached."
                        " Stop and ask the user: ship (run /supervisor:start), spike (write .supervisor/explore.md and stop),"
                        " or drop. Further tool calls are allowed; continue only with their answer."
                    ),
                }
            }
    elif gated and spend >= budget and not cheap_delegation:
        raise_clause = " Context is preserved: " + raise_advice(cfg)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"supervisor: expensive-tier spend ${spend:.2f} has reached the session budget ${budget:.2f}."
                    + raise_clause
                    + " Spawning cheap workers (supervisor:implementer, supervisor:scout)"
                    " is still allowed. Write down the state first if the next step is a decision."
                ),
            }
        }
    out: Dict[str, Any] = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if gated and budget > 0 and spend >= budget * float(cfg["warn_at"]) and not ledger.state["warned"]:
        ledger.state["warned"] = True
        out["systemMessage"] = f"supervisor: ${spend:.2f} of the ${budget:.2f} expensive-tier budget used. Delegate what remains; keep the conductor's turns short."

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


def h_post_tool_use(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger, project_dir: Optional[str] = None) -> Dict[str, Any]:
    """A spawn that died: name it in the ledger and tell the conductor, in the
    same turn, whether to retry once or change tier. The ``tool_response`` of
    a failed foreground Agent call is not field-verified, so any shape that
    does not carry DEATH_RE's phrase is a no-op rather than a guess."""
    ledger.note_hook_context(hook)
    ledger.update(hook.get("transcript_path"))
    mode = effective_mode(cfg, ledger)
    if mode == "off":
        return {}
    if hook.get("tool_name") != "Agent":
        return {}
    text = response_text(hook.get("tool_response")).strip()
    if not text or len(text) > DEATH_MAX_CHARS or not DEATH_RE.match(text):
        return {}
    tool_input = hook.get("tool_input") or {}
    sub = str(tool_input.get("subagent_type") or "general-purpose")
    explicit = tool_input.get("model")
    model = explicit if explicit and explicit != "inherit" else declared_model(sub, cfg, project_dir)
    if model is None:
        # The same resolution agent_policy applied when it let the spawn
        # through: pinned to the worker model, or inherited. That is the
        # model the worker ran on, not a guess at the conductor's tier.
        session_model = ledger.main_model()
        if cfg["always_pin_workers"] or is_expensive(session_model, cfg) or session_model is None:
            model = cfg["worker_model"]
        else:
            model = session_model
    model = clean_label(model or "unknown")
    sub = clean_label(sub)
    kind = death_kind(text)
    # A plan-wide session limit names no model and takes every tier with it;
    # advising another tier there would spawn the next death.
    plan_wide = kind == "quota" and re.search(r"session limit", text, re.I) is not None
    display = text
    head = one_line(display, 120)
    ledger.state["deaths"].append({"type": sub, "model": model, "kind": kind, "error": head, "ts": time.time()})
    if kind == "quota":
        ledger.note_quota_hit(model, head)
    if mode == "observe":
        return {}
    short = one_line(display, 80)
    if kind == "transient":
        msg = (
            f"supervisor: worker '{sub}' died on a transient API error ({short})."
            " Wait about 60 s, then re-spawn it once with the same spec; if it dies again,"
            " stop and record the state on the driving issue. Do not finish the slice inline."
        )
    elif plan_wide:
        msg = (
            f"supervisor: worker '{sub}' died because the plan's session limit is hit ({short})."
            " Every tier is out until the reset, this session's own calls included. Write the"
            f" state down and wait for the reset; spawns onto {model} are denied meanwhile."
        )
    elif kind == "quota":
        msg = (
            f"supervisor: worker '{sub}' died because the {model} usage limit is hit ({short})."
            f" Spawns onto {model} are now denied for this session. Delegate to another tier"
            " (supervisor:implementer on sonnet, supervisor:senior-implementer on opus) or wait"
            f" for the reset. Do not retry on {model}."
        )
    else:
        msg = (
            f"supervisor: worker '{sub}' died ({short})."
            " Read what it left on disk before deciding; do not finish the slice inline."
        )
    # additionalContext is the channel the model reads (PostToolUse: "added to
    # Claude's context alongside the tool result", hooks reference, checked
    # 2026-09-04); systemMessage is shown to the person. Both get the advice.
    return {
        "systemMessage": msg,
        "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg},
    }


def h_subagent_stop(hook: Dict[str, Any], cfg: Dict[str, Any], ledger: Ledger) -> Dict[str, Any]:
    ledger.update(hook.get("transcript_path"))
    mode = effective_mode(cfg, ledger)
    if mode == "off":
        return {}
    if not cfg["enforce_reports"] or mode in ("observe", "explore"):
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
        return {"systemMessage": f"supervisor: accepted {atype} report after {blocks} block(s) without a full contract; verify its evidence yourself."}
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
            f"supervisor: your final report does not meet the '{contract}' contract: "
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
                "mode": effective_mode(cfg, ledger),
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
        print("supervisor: no session ledger yet (hooks have not run in this session).")
        return 0
    ledger = Ledger(sid, Pricing.load())
    tp = _arg(args, "--transcript")
    if tp:
        ledger.update(tp)
        ledger.save()
    print(ledger.report(cfg, os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()))
    return 0


def _scope(args: List[str]) -> Optional[str]:
    return "project" if "--project" in args else "user" if "--user" in args else None


def write_setting(key: str, value: Any, project_dir: Optional[str], scope: Optional[str]) -> Path:
    """Write one config key. scope None: this project's entry under
    'projects' in the user's file (the default, and the only place a raise
    or a mode change can come from); 'user': the user's file top level;
    'project': the project's .claude/supervisor.json, which may only tighten."""
    if scope == "project":
        target = Path(project_dir or ".") / ".claude" / CONFIG_FILENAME
    else:
        target = Path.home() / ".claude" / CONFIG_FILENAME
    legacy = target.parent / LEGACY_CONFIG_FILENAME
    if not target.exists() and legacy.exists():
        legacy.rename(target)
        print(f"renamed {legacy} to {target}")
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
    """Show or set the supervisor mode. Usage: supervisor.py mode [show|explore|enforce|observe|off] [--user|--project]"""
    usage = "usage: supervisor.py mode [show|explore|enforce|observe|off] [--user|--project]"
    verb = next((a for a in args if not a.startswith("--")), "show")
    if verb == "show":
        print(f"mode: {cfg['mode']} (config)")
        sid = os.environ.get("CLAUDE_SESSION_ID") or _latest_session_id()
        eff = effective_mode(cfg, Ledger(sid, Pricing.load())) if sid else cfg["mode"]
        print("session: dormant" if eff == "off" else f"session: armed {eff}")
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
        raw = cfg["budget_usd"]
        eff = effective_budget(cfg)
        ceiling = cfg.get("budget_ceiling_usd")
        if ceiling is not None and eff < float(raw):
            budget_str = f"budget_usd: {raw} → effective {eff} (ceiling)"
        else:
            pname = profile_name(cfg)
            budget_str = f"budget_usd: {raw}" + (f" ({pname})" if pname else "")
        print(f"{budget_str}  worker_model: {cfg['worker_model']}  allow_fork: {cfg['allow_fork']}  route_general_purpose: {cfg['route_general_purpose']}  max_expensive_spawns: {cfg['max_expensive_spawns']}")
        profiles_txt = " ".join(f"{name}={val}" for name, val in (cfg.get("budget_profiles") or {}).items())
        print(f"profiles: {profiles_txt}")
        print(f"ceiling: {'none' if ceiling is None else ceiling}")
        print("config files (low to high precedence): " + ", ".join(str(p) for p in config_paths(project_dir)))
        print("project files may only tighten: " + ", ".join(sorted(TIGHTEN_ONLY)))
        for note in cfg.get("_ignored", []):
            print(f"ignored: {note}")
        return 0
    if args[0] == "set" and len(args) >= 2:
        word = args[1]
        profiles = cfg.get("budget_profiles") or {}
        # A number is tried first: a profile table that happens to use a
        # numeral as a name (e.g. a project's own "5") must never shadow the
        # number itself.
        try:
            value: Optional[float] = float(word)
        except ValueError:
            value = None
        if value is None:
            value = float(profiles[word]) if word in profiles else float("nan")
        if not math.isfinite(value) or value < 0:
            names = ", ".join(sorted(profiles))
            print(f"usage: supervisor.py budget set <usd|profile> [--user|--project] — a finite number, 0 or more"
                  f" (0 closes the gate), or one of the named profiles: {names}")
            return 2
        # Default: this project's entry in the user's own file, which is the
        # only place a raise can come from (a project file may only tighten).
        # The value written is always the one asked for: a ceiling narrows
        # what takes effect (effective_budget), it never rewrites budget_usd.
        target = write_setting("budget_usd", value, project_dir, _scope(args))
        effective = load_config(project_dir)
        if float(effective["budget_usd"]) != value:
            print(f"budget_usd={value} written to {target}, but the effective budget is {effective['budget_usd']}:"
                  " another config file wins (see 'budget show'). " + "; ".join(effective.get("_ignored", [])[-2:]))
            return 1
        pname = profile_name(dict(effective, budget_usd=value, budget_ceiling_usd=None))
        suffix = f" ({pname})" if pname else ""
        eff_budget = effective_budget(effective)
        if eff_budget < value:
            print(f"budget_usd={value}{suffix} written to {target}; effective budget is {eff_budget} (ceiling)."
                  " Applies from the next tool call.")
            return 0
        print(f"budget_usd={value}{suffix} written to {target}. Applies from the next tool call.")
        return 0
    if args[0] == "ceiling" and len(args) >= 2:
        word = args[1]
        if word == "off":
            value = None
        else:
            try:
                value = float(word)
            except ValueError:
                value = float("nan")
            if not math.isfinite(value) or value < 0:
                print("usage: supervisor.py budget ceiling <usd|off> [--user|--project] — a finite number, 0 or more, or 'off'")
                return 2
        # A ceiling is a personal cap: default to the user's top level, not
        # this project's entry, so --user is the implied scope.
        scope = _scope(args) or "user"
        target = write_setting("budget_ceiling_usd", value, project_dir, scope)
        effective = load_config(project_dir)
        if effective.get("budget_ceiling_usd") != value:
            print(f"budget_ceiling_usd={value} written to {target}, but the effective ceiling is"
                  f" {effective.get('budget_ceiling_usd')}: another config file wins (see 'budget show')."
                  " " + "; ".join(effective.get("_ignored", [])[-2:]))
            return 1
        print(f"budget_ceiling_usd={value} written to {target}. Applies from the next tool call.")
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
    print("usage: supervisor.py budget [show|set <usd|profile> [--user|--project]|ceiling <usd|off> [--user|--project]|history]")
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
    parts = [f"supervisor {model}"]
    if sid:
        led = Ledger(sid, Pricing.load())
        mode = effective_mode(cfg, led)
        if mode == "off":
            state = "dormant"
        elif cfg["enforce_budget"] and led.expensive_spend(cfg) >= effective_budget(cfg) and is_expensive(led.main_model(), cfg):
            state = "CLOSED"
        else:
            state = f"${led.expensive_spend(cfg):.2f}/${effective_budget(cfg):.0f}"
        parts.append(f"fable {state}")
        parts.append(f"total ${led.total_spend():.2f}")
        n = len([x for x in led.state["spawns"] if not x["action"].startswith("deny")])
        if n:
            parts.append(f"spawns {n}")
    else:
        mode = None
    workers = worker_spend((data.get("workspace") or {}).get("current_dir") or data.get("cwd") or os.getcwd())
    if workers >= 0.005:
        parts.append(f"workers ${workers:.2f}")
    if isinstance(claude_cost, (int, float)):
        parts.append(f"claude ${claude_cost:.2f}")
    if isinstance(ctx, (int, float)):
        parts.append(f"ctx {int(ctx)}%")
    if mode == "observe":
        parts.append("observe")
    print(" · ".join(parts))
    return 0


def cmd_statusline_snippet() -> int:
    """The settings.json fragment that installs the status line. Printed, not
    written: settings are the user's to change."""
    cmd = f'python3 "{HERE / "supervisor.py"}" statusline'
    print(json.dumps({"statusLine": {"type": "command", "command": cmd, "padding": 1}}, indent=2))
    print("\nMerge into ~/.claude/settings.json (or the project's .claude/settings.json). The path above is this install's;", file=sys.stderr)
    print("plugin updates move it, so re-run `supervisor.py statusline-snippet` after an update.", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- deterministic helpers for the skills

RESULT_RE = re.compile(r"^#{1,6}\s*Result\b[^\n]*(?:\n\s*(?:\*\*)?)?(DONE|PARTIAL|BLOCKED)\b", re.M | re.I)


def cmd_check_report(args: List[str], cfg: Dict[str, Any]) -> int:
    """Check a worker report against a contract, the same way SubagentStop does.
    Usage: supervisor.py check-report [FILE|-] --contract worker|scout|reviewer [--transcript AGENT.jsonl]"""
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
    """supervisor.py brief check [FILE|-]  |  supervisor.py brief template
    The lint the brief skill runs on .supervisor/brief.md, and the format it fills in."""
    if not args or args[0] not in ("check", "template"):
        print("usage: supervisor.py brief check [FILE|-] | brief template")
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
    bad = [i for i in ids if not ID_RE.match(i)]
    if bad:
        # An id becomes a directory name and a branch name; keep it to one
        # component of letters, digits, '.', '_' and '-', not starting with '.'.
        errors.append("slice ids must match [A-Za-z0-9][A-Za-z0-9._-]*: " + ", ".join(clean_label(b) for b in bad))
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
    """supervisor.py plan build SLICES.json [--name N] [--out DIR]  |  supervisor.py plan check PLAN.json
    SLICES.json: [{"id", "files": [...], "deps": [...], "command", "dod"}, ...]"""
    if not args or args[0] not in ("build", "check") or len(args) < 2:
        print("usage: supervisor.py plan build SLICES.json [--name NAME] [--out DIR] | plan check PLAN.json")
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
    if args[0] == "check" and isinstance(data, dict) and "levels" in data:
        # run-level trusts the levels key verbatim, so check says whether it still matches the slices.
        given = data["levels"]
        flat = [str(i) for lvl in (given if isinstance(given, list) else []) for i in (lvl if isinstance(lvl, list) else [])]
        if len(set(flat)) != len(flat):
            errors.append("levels repeat a slice id")
        known = {str(x.get("id")) for x in slices}
        if set(flat) != known:
            errors.append("levels do not name exactly the plan's slices; rebuild with 'plan build'")
        elif [[str(i) for i in lvl] for lvl in given] != levels:
            errors.append("levels differ from the order the dependencies require; rebuild with 'plan build'")
    if errors:
        print("PLAN INVALID")
        for e in errors:
            print(f"- {e}")
        return 1
    if args[0] == "check":
        print(f"PLAN OK: {len(slices)} slices in {len(levels)} levels")
        return 0
    name = _arg(args, "--name") or (data.get("name") if isinstance(data, dict) else None) or Path(args[1]).stem
    out = Path(_arg(args, "--out") or ".supervisor")
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.md").write_text(render_plan(str(name), slices, levels))
    (out / "plan.json").write_text(json.dumps({"name": path_label(str(name)), "slices": slices, "levels": levels}, indent=2) + "\n")
    print(f"PLAN OK: {len(slices)} slices in {len(levels)} levels -> {out / 'plan.md'}, {out / 'plan.json'}")
    for n, lvl in enumerate(levels):
        print(f"  level {n}: {', '.join(lvl)}")
    return 0


# Tools a headless worker may use without a prompt. Print mode cannot answer a
# permission prompt, so anything outside this list is denied; widen it per
# project with worker_allowed_tools in supervisor.json.
DEFAULT_WORKER_TOOLS = [
    "Read", "Edit", "Write", "Grep", "Glob",
    "Bash(pytest *)", "Bash(python *)", "Bash(python3 *)", "Bash(uv run *)", "Bash(uv sync*)",
    "Bash(npm test*)", "Bash(npx *)", "Bash(ls *)", "Bash(cat *)", "Bash(git diff*)", "Bash(git status*)", "Bash(git log*)",
]


# Extra attempts after the first, per slice and per run-level invocation,
# before it is FAILED. The CLI already retries retryable API errors inside
# one run; this is the outer retry for a process that still died on
# overload, which happened four times in one afternoon. A rerun gets the
# same allowance again: the index keeps the cumulative count for the record.
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
# 500 is in the list because one of the 13 worker deaths measured on
# 2026-09-04 was "API Error: 500 Internal server error", which the API's own
# message calls temporary.
TRANSIENT_RE = re.compile(r"overloaded|rate.?limit|\b529\b|\b503\b|\b502\b|\b500\b|internal server error|server_error|connection (?:reset|error)|ECONNRESET", re.I)
# A usage limit, which is the opposite of transient: waiting a minute and
# re-spawning burns the next worker the same way, so it is classified first
# and denies the model for the session. The wordings are the ones seen on
# this machine: "You've reached your Fable 5 limit. Run /usage-credits to
# continue or switch models with /model." and "You've hit your session limit
# · resets 1:50pm (America/New_York)".
QUOTA_RE = re.compile(r"reached your .{0,40}limit|hit your session limit|usage limit|/usage-credits|resets \d", re.I)
# What the Agent tool returns when a spawned worker did not finish because
# the API failed under it: the phrase seen in conductor transcripts on
# 2026-08-29 and 2026-09-03, on its own or behind the notification prefix
# 'Agent "<description>" failed: '. It must open the response: a worker that
# finished and merely quotes the phrase in its report (a worker writing about
# this very feature did) must not be told to retry work that completed.
DEATH_RE = re.compile(r'(?:Agent\s+"[^"\n]{0,200}"\s+failed:\s*)?Agent terminated early due to an API error', re.I)
# A death message is one line; the longest seen is under 200 characters. A
# response longer than this is a report, whatever it quotes.
DEATH_MAX_CHARS = 600
VERDICTS = ("DONE", "PARTIAL", "BLOCKED", "NONCOMPLIANT", "FAILED")


# A spec longer than this is two slices (delegate skill: "keep it under a
# page"); the hard cap is the brief cap, past which it is pasting material.
SPEC_SOFT_LINES = 80
# The hard cap on a spec. Its own knob, not the consult brief's: tightening
# one must not make every slice fail at dispatch. 8000 chars is two pages.
SPEC_MAX_CHARS = 8000
# A worktree setup command (an environment install) that runs longer than
# this is a problem to look at, not to wait for.
SETUP_TIMEOUT_S = 600.0
# Below this much remaining budget a retry cannot do anything but die again.
WORKER_MIN_BUDGET_USD = 0.05
# More done items than this, or more files to change, and the slice is
# doing several jobs (decompose skill: one to five files per slice).
SPEC_MAX_DONE = 6
SPEC_MAX_FILES = 5
# Kinds of work; a spec that mixes three or more ran a worker out of turns
# in the field. Each pattern names the kind for the message. A keyword
# heuristic: it catches the common wordings, not every phrasing, and says so.
SPEC_KINDS = (
    ("investigate", re.compile(r"\b(investigat\w*|explor\w*|survey|review (?:the )?(?:diff|changes?)|(?:see|look at|check|find out) what changed|what changed|understand|audit|assess|analy[sz]e)\b", re.I)),
    ("upgrade", re.compile(r"\b(upgrad\w*|updat\w* (?:the )?(?:pin|pinned|version|dependency)|bump|re-?pin|migrat\w*|(?:move|switch|bring)\b[^\n.]{0,40}\b(?:new|latest) version|(?:new|latest) version)\b", re.I)),
    ("regression", re.compile(r"\b(regression|(?:old|existing|current) (?:tests?|suite) (?:still )?pass\w*|re-?run (?:the )?(?:old|existing|full) (?:tests?|suite)|still pass\w*|nothing (?:else )?breaks)\b", re.I)),
    ("new tests", re.compile(r"\b(write|add|create|build)\b[^\n.]{0,40}\b(tests?|coverage)\b|\btest coverage\b|\bcover\w*\b[^\n.]{0,40}\b(?:with )?tests?\b|\btests? for (?:the )?new\b", re.I)),
    ("quality gates", re.compile(r"\b(lint\w*|quality gates?|type-?check\w*|types? (?:clean|errors?)|mypy|pyright|ruff|black|formatter|pre-commit)\b", re.I)),
    ("rewrite", re.compile(r"\b(rewrite|rework|refactor\w*|restructure)\b", re.I)),
)
# A lookup told NOT to happen is not a lookup.
SPEC_NEGATION_RE = re.compile(r"\b(do not|don'?t|never|no need to|without)\b", re.I)
# A line that asks the worker to find a value the conductor could resolve in
# one command: several worker turns spent on a lookup, in the field.
SPEC_LOOKUP_RE = re.compile(r"\b(find|locate|figure out|determine|discover|look up|work out)\b[^\n]{0,60}\b(path|directory|dir|folder|file|config\w*|value|version|where)\b", re.I)


def spec_check_problems(text: str, cfg: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """(errors, warnings) for a slice spec before dispatch. Errors stop the
    dispatch: an empty spec, or one over the size cap. Warnings are the
    signs a spec is two slices or carries a lookup the conductor owed."""
    errors: List[str] = []
    warnings: List[str] = []
    if not text.strip():
        return ["spec is empty"], []
    if len(text) > SPEC_MAX_CHARS:
        errors.append(f"spec is {len(text)} chars, cap {SPEC_MAX_CHARS}: point at files instead of pasting them, or split")
    lines = text.splitlines()
    if len(lines) > SPEC_SOFT_LINES:
        warnings.append(f"spec is {len(lines)} lines, more than {SPEC_SOFT_LINES}: a spec that needs more is two slices")
    if not re.search(r"^\s*(\*\*Goal|#{1,6}\s*Goal)", text, re.M | re.I):
        warnings.append("no Goal line: one sentence, the observable outcome")
    for h in ("Files", "Definition of done", "Tests to run", "Out of scope"):
        if not has_heading(text, h):
            warnings.append(f"missing '## {h}'")
    done = done_items(section_body(text, "Definition of done"))
    if len(done) > SPEC_MAX_DONE:
        warnings.append(f"{len(done)} done items, more than {SPEC_MAX_DONE}: split the slice")
    files_body = section_body(text, "Files")
    cut = re.search(r"(?im)^.*\b(leave alone|do not (?:touch|change)|untouched|not in scope)\b.*$", files_body)
    change = files_body[:cut.start()] if cut else files_body
    nfiles = len([ln for ln in change.splitlines() if re.match(r"^\s*[-*]\s+`", ln)])
    if nfiles > SPEC_MAX_FILES:
        warnings.append(f"{nfiles} files to change, more than {SPEC_MAX_FILES}: split the slice")
    # Kinds and lookups are judged on the work itself: the goal, the files
    # and the definition of done; not on the sections that narrow the slice.
    first_heading = re.search(r"^#{1,6}\s", text[1:], re.M)
    goal = text[: first_heading.start() + 1] if first_heading else text
    work = "\n".join([goal, files_body, section_body(text, "Definition of done")])
    kinds = [name for name, rx in SPEC_KINDS if rx.search(work)]
    if len(kinds) >= 3:
        warnings.append("mixes " + ", ".join(kinds) + ": three or more kinds of work in one slice ran a worker out of turns; split")
    for ln in work.splitlines():
        m = SPEC_LOOKUP_RE.search(ln)
        if m and not ln.lstrip().startswith("#") and not SPEC_NEGATION_RE.search(ln[: m.start()]):
            warnings.append("asks the worker to resolve a value the conductor can: '" + one_line(ln, 80) + "'")
            break
    return errors, warnings


def cmd_spec(args: List[str], cfg: Dict[str, Any]) -> int:
    """supervisor.py spec check FILE|- : errors stop dispatch (exit 1), warnings are printed with '! '."""
    usage = "usage: supervisor.py spec check FILE|-"
    if not args or args[0] != "check":
        print(usage)
        return 2
    src = next((a for a in args[1:] if not a.startswith("--")), "-")
    if src == "-" and sys.stdin.isatty():
        print(usage)
        return 2
    try:
        text = sys.stdin.read() if src == "-" else Path(src).read_text()
    except (OSError, ValueError) as e:
        print(f"NONCOMPLIANT spec={src}")
        print(f"- cannot read {src}: {e}")
        return 1
    errors, warnings = spec_check_problems(text, cfg)
    print(f"{'OK' if not errors else 'NONCOMPLIANT'} spec={src}")
    for e in errors:
        print(f"- {e}")
    for w in warnings:
        print(f"! {w}")
    return 0 if not errors else 1


def parse_worker_output(stdout: str) -> Tuple[str, Dict[str, Any]]:
    """`claude -p --output-format json` prints one object: on success the text
    is under 'result'; an error result (subtype error_*, is_error true, an
    'errors' list, still with total_cost_usd and session_id) has no 'result'
    key at all (observed on 2.1.258). Anything that is not such an object
    (older CLI, a fake in tests) is taken as the text."""
    try:
        obj = json.loads(stdout)
    except ValueError:
        return stdout, {}
    if isinstance(obj, dict) and (obj.get("type") == "result" or "result" in obj or "is_error" in obj or "subtype" in obj):
        res = obj.get("result")
        return (res if isinstance(res, str) else ("" if res is None else json.dumps(res))), obj
    return stdout, {}


def run_worker_once(spec_path: str, agent: str, budget: str, out_dir: Path, cfg: Dict[str, Any],
                    cwd: Optional[str] = None, timeout: Optional[float] = None, resume: Optional[str] = None,
                    slug: Optional[str] = None, attempt: int = 1, dry_run: bool = False) -> Dict[str, Any]:
    """One headless worker run. Returns verdict (one of VERDICTS), problems,
    report path, cost, session_id, transient (the failure was the API, a
    retry may help) and error. The conductor never sees the worker's tool
    output: only the verdict line and the report file."""
    out: Dict[str, Any] = {"verdict": "FAILED", "problems": [], "report": None, "cost": 0.0, "cost_known": False, "session_id": None, "transient": False, "error": "", "warnings": []}
    try:
        spec = Path(spec_path).read_text()
    except (OSError, ValueError) as e:
        out["error"] = f"cannot read spec: {e}"
        return out
    errors, warnings = spec_check_problems(spec, cfg)
    out["warnings"] = warnings
    if errors:
        out["error"] = "spec check: " + "; ".join(errors)
        return out
    short = agent.split(":", 1)[-1]
    contract = cfg["report_contracts"].get(short, "worker")
    tools = cfg.get("worker_allowed_tools") or DEFAULT_WORKER_TOOLS
    slug = path_label(slug or Path(spec_path).stem)
    # The attempt is in the name so a retry never overwrites the evidence of why the first one died.
    report_path = out_dir / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}-a{int(attempt)}.md"
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
    out["report_path"] = str(report_path)
    if dry_run:
        return out
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
    try:
        c = float(meta["total_cost_usd"])
        out["cost"] = c if math.isfinite(c) and c >= 0 else 0.0  # NaN, inf and negatives would poison every later sum
        out["cost_known"] = math.isfinite(c) and c >= 0
    except (TypeError, ValueError, KeyError):
        out["cost"] = 0.0  # untrusted or absent: the caller decides what to charge
    sid = meta.get("session_id")
    out["session_id"] = str(sid) if isinstance(sid, (str, int)) else None
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text + (f"\n\n<!-- stderr -->\n{proc.stderr[-4000:]}" if proc.stderr.strip() else ""))
    out["report"] = str(report_path)
    died = (proc.returncode != 0 and not text.strip()) or bool(meta.get("is_error"))
    if died:
        # Diagnostics only: the CLI's subtype and errors list, and stderr. Never
        # the worker's own text, which may discuss overloads without having had one.
        errs = meta.get("errors")
        diag = " ".join([str(meta.get("subtype") or ""), " ".join(str(e) for e in errs) if isinstance(errs, list) else str(errs or ""), proc.stderr.strip()]).strip()
        tail = one_line((diag or text.strip())[-300:], 200)
        out["error"] = (f"claude reported an error: {tail}" if proc.returncode == 0 else f"claude exited {proc.returncode}: {tail}")
        out["transient"] = bool(TRANSIENT_RE.search(diag))
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
    Usage: supervisor.py run-worker --spec PATH [--agent supervisor:implementer] [--budget 2] [--out DIR] [--resume SESSION] [--dry-run]"""
    spec_path = _arg(args, "--spec")
    if not spec_path:
        print("usage: supervisor.py run-worker --spec PATH [--agent NAME] [--budget USD] [--out DIR] [--resume SESSION] [--dry-run]")
        return 2
    agent = _arg(args, "--agent") or "supervisor:implementer"
    budget = _arg(args, "--budget") or str(cfg.get("worker_budget_usd", 2.0))
    try:
        if not math.isfinite(float(budget)) or float(budget) <= 0:
            raise ValueError
    except ValueError:
        print("--budget must be a positive number")
        return 2
    out_dir = Path(_arg(args, "--out") or (Path(project_dir or ".") / ".supervisor" / "runs"))
    r = run_worker_once(spec_path, agent, budget, out_dir, cfg, resume=_arg(args, "--resume"), dry_run="--dry-run" in args)
    if "--dry-run" not in args and r.get("report"):
        # One index entry per run, under the project's runs directory whatever
        # --out was, so worker_spend and `runs` see single-slice dispatches too.
        idx_path = Path(project_dir or ".").resolve() / ".supervisor" / "runs" / "run-worker" / "level-0.json"
        idx = load_index(idx_path) or {"plan": "run-worker", "level": 0, "slices": {}}
        key = Path(r["report"]).stem
        idx["slices"][key] = {"state": "done" if r["verdict"] == "DONE" else "failed", "verdict": r["verdict"], "attempts": 1,
                              "cost": float(r.get("cost") or 0.0), "report": r["report"], "session_id": r.get("session_id"), "error": one_line(r.get("error") or "", 300)}
        try:
            idx_path.parent.mkdir(parents=True, exist_ok=True)
            idx_path.write_text(json.dumps(idx, indent=2) + "\n")
        except OSError as e:
            log_error(f"run-worker index not written: {e}")
    if "--dry-run" in args:
        if not r.get("cmd"):
            print(f"ERROR {r['error']}")
            return 2
        print("DRY-RUN " + " ".join(r["cmd"]))
        print(f"report -> {r['report_path']}")
        for w in r.get("warnings") or []:
            print(f"SPEC WARNING {one_line(w, 200)}")
        return 0
    if r["verdict"] == "FAILED" and not r["report"]:
        print(f"ERROR {one_line(r['error'], 300)}")
        return 2 if "spec" in r["error"] or "PATH" in r["error"] else 1
    print(f"VERDICT: {r['verdict']} agent={agent} budget=${budget} cost=${r['cost']:.2f} report={r['report']}"
          + (f" session={r['session_id']}" if r.get("session_id") else ""))
    if r["error"]:
        print(f"- {one_line(r['error'], 200)}" + (" (transient)" if r["transient"] else ""))
    for pr in r["problems"]:
        print(f"- {one_line(pr, 200)}")
    for w in r.get("warnings") or []:
        print(f"SPEC WARNING {one_line(w, 200)}")  # after the verdict: the first line is the contract
    return 0 if r["verdict"] == "DONE" else 1


# --------------------------------------------------------------------------- supervised levels (supervisor.py run-level)


def _git(root: Path, *args: str) -> Tuple[int, str]:
    import subprocess
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    return p.returncode, (p.stdout + p.stderr).strip()


def ensure_exclude(root: Path) -> None:
    """Keep .supervisor/ (worktrees, runs, specs) out of the parent repository's
    index without touching a tracked file: .git/info/exclude, once. Skipped
    when .git is not a directory (the root is itself a worktree)."""
    info = root / ".git" / "info"
    if not (root / ".git").is_dir():
        return
    try:
        info.mkdir(parents=True, exist_ok=True)
        ex = info / "exclude"
        lines = ex.read_text().splitlines() if ex.exists() else []
        if ".supervisor/" not in [l.strip() for l in lines]:
            with ex.open("a") as f:
                f.write(("" if not lines or lines[-1] == "" else "\n") + ".supervisor/\n")
    except OSError as e:
        log_error(f"exclude not written: {e}")


def ensure_worktree(root: Path, plan: str, slice_id: str) -> Tuple[Optional[Path], str, bool]:
    """A worktree per slice under .supervisor/wt/<plan>/<slice> on branch
    <plan>/<slice>. Namespaced by plan, as the run index is: two plans may
    reuse a slice id and must never share a checkout. An existing directory
    is reused only when it is on that branch."""
    path = root / ".supervisor" / "wt" / path_label(plan) / path_label(slice_id)
    branch = f"{path_label(plan)}/{path_label(slice_id)}"
    if path.exists():
        rc, head = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
        if rc != 0 or head.strip() != branch:
            return None, f"{path} exists but is on {head.strip() or '?'}, not {branch}", False
        return path, branch, False
    ensure_exclude(root)
    _git(root, "worktree", "prune")  # a registration whose directory is gone would refuse the add
    rc, _ = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    if rc == 0:
        rc, msg = _git(root, "worktree", "add", str(path), branch)
    else:
        rc, msg = _git(root, "worktree", "add", "-b", branch, str(path), "HEAD")
    if rc != 0:
        return None, msg, False
    return path, branch, True


def spec_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


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
    Usage: supervisor.py run-level PLAN.json --level N [--parallel K] [--budget USD] [--retries N] [--backoff S]
           [--timeout S] [--agent NAME] [--specs DIR] [--no-worktree] [--dry-run]
    Each slice: its own worktree, a bounded number of attempts with backoff on a transient
    failure, one VERDICT line. The index under .supervisor/runs/<plan>/ makes a rerun resume."""
    usage = "usage: supervisor.py run-level PLAN.json --level N [--parallel K] [--budget USD] [--retries N] [--backoff S] [--timeout S] [--agent NAME] [--specs DIR] [--setup CMD] [--no-worktree] [--dry-run]"
    valued = ("--level", "--parallel", "--budget", "--retries", "--backoff", "--timeout", "--agent", "--specs", "--setup")
    values = {args[i + 1] for i, a in enumerate(args[:-1]) if a in valued}
    plan_path = next((a for a in args if not a.startswith("--") and a.endswith(".json") and a not in values), None)
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
    name = path_label(str(plan.get("name") or Path(plan_path).stem))
    # Two ids that map to one label would share a worktree; refuse before anything runs.
    labels: Dict[str, str] = {}
    seen: set = set()
    for sid in ids:
        if sid in seen or sid not in slices:
            print(f"slice id {clean_label(sid)!r} is repeated in level {level} or is not in the plan's slices; run 'plan check'")
            return 2
        seen.add(sid)
        if not ID_RE.match(sid) or labels.setdefault(path_label(sid), sid) != sid:
            print(f"slice id {clean_label(sid)!r} is not a safe path component or collides with {clean_label(labels.get(path_label(sid), ''))!r}; ids must match [A-Za-z0-9][A-Za-z0-9._-]*")
            return 2
    specs_dir = Path(_arg(args, "--specs") or (root / ".supervisor" / "specs"))
    out_dir = root / ".supervisor" / "runs" / name
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
    agent_default = _arg(args, "--agent") or "supervisor:implementer"
    use_worktree = "--no-worktree" not in args
    setup_cmd = _arg(args, "--setup")  # run once in each newly created worktree, e.g. "uv sync": a worker needs the project's environment
    if setup_cmd and not use_worktree:
        print("NOTE --setup is ignored with --no-worktree: the checkout is the project's own")

    index = load_index(index_path) or {"plan": name, "level": level, "slices": {}}
    for sid in ids:
        index["slices"].setdefault(sid, {"state": "pending", "attempts": 0, "verdict": None, "cost": 0.0, "report": None, "session_id": None, "worktree": None, "branch": None, "error": ""})
    # A DONE slice is skipped only if the spec it was earned against is unchanged.
    todo, skipped, changed = [], [], []
    for sid in ids:
        e = index["slices"][sid]
        if e.get("verdict") == "DONE":
            if e.get("spec_sha") == spec_digest(specs_dir / f"{sid}.md"):
                skipped.append(sid)
                continue
            changed.append(sid)
        todo.append(sid)

    if "--dry-run" in args:
        for sid in ids:
            spec = specs_dir / f"{sid}.md"
            print(f"{'SKIP' if sid in skipped else 'RUN '} slice={sid} spec={spec}{'' if spec.exists() else ' (missing)'} agent={slices[sid].get('agent') or agent_default}"
                  + (f" worktree={root / '.supervisor' / 'wt' / path_label(name) / path_label(sid)}" if use_worktree else ""))
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
        # Fail closed per slice: an exception here must not take the level
        # down or leave the index saying "running" forever.
        try:
            return _run_slice(sid)
        except Exception as e:  # noqa: BLE001
            with lock:
                index["slices"][sid].update(state="failed", verdict="FAILED", error=one_line(f"{type(e).__name__}: {e}", 300))
                save()
                print(f"VERDICT: FAILED slice={sid} attempts={index['slices'][sid].get('attempts') or 0} cost=${float(index['slices'][sid].get('cost') or 0):.2f} error={one_line(f'{type(e).__name__}: {e}', 120)}")
            log_error(f"run-level {name}/{level} slice {sid}: {type(e).__name__}: {e}")
            return "FAILED"

    def _run_slice(sid: str) -> str:
        entry = index["slices"][sid]
        spec = specs_dir / f"{sid}.md"
        agent = str(slices[sid].get("agent") or agent_default)
        if not spec.exists():
            with lock:
                entry.update(state="failed", verdict="FAILED", error=f"no spec at {spec}")
                save()
                print(f"VERDICT: FAILED slice={sid} attempts=0 cost=$0.00 error=no spec at {spec}")
            return "FAILED"
        # The spec check needs only the text: refuse before a worktree or a setup is paid for.
        try:
            spec_errors, spec_warnings = spec_check_problems(spec.read_text(), cfg)
        except (OSError, ValueError) as e:
            spec_errors, spec_warnings = [f"cannot read spec: {e}"], []
        with lock:
            for w in spec_warnings:
                print(f"SPEC WARNING slice={sid} {one_line(w, 160)}")
        if spec_errors:
            with lock:
                entry.update(state="failed", verdict="FAILED", error=one_line("spec check: " + "; ".join(spec_errors), 300))
                save()
                print(f"VERDICT: FAILED slice={sid} attempts=0 cost=$0.00 error={one_line('spec check: ' + '; '.join(spec_errors), 120)}")
            return "FAILED"
        cwd: Optional[str] = None
        if use_worktree:
            with lock:
                wt, branch_or_msg, created = ensure_worktree(root, name, sid)
            if wt is None:
                with lock:
                    entry.update(state="failed", verdict="FAILED", error=one_line(f"worktree: {branch_or_msg[-200:]}", 300))
                    save()
                    print(f"VERDICT: FAILED slice={sid} attempts=0 cost=$0.00 error={one_line('worktree: ' + branch_or_msg[-120:], 120)}")
                return "FAILED"
            cwd = str(wt)
            with lock:
                entry.update(worktree=str(wt), branch=branch_or_msg)
            # Setup runs until it has succeeded once for this worktree: a failed
            # setup leaves the directory behind, and a rerun must not skip it.
            if setup_cmd and (created or entry.get("setup") != "ok"):
                import subprocess
                try:
                    sp = subprocess.run(setup_cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=SETUP_TIMEOUT_S, check=False)
                    rc_setup, tail = sp.returncode, one_line((sp.stderr or sp.stdout)[-300:], 160)
                except subprocess.TimeoutExpired:
                    rc_setup, tail = 124, f"setup timed out after {SETUP_TIMEOUT_S:.0f}s"
                with lock:
                    entry["setup"] = "ok" if rc_setup == 0 else "failed"
                    save()
                if rc_setup != 0:
                    with lock:
                        entry.update(state="failed", verdict="FAILED", error=one_line(f"setup failed ({rc_setup}): {tail}", 300))
                        save()
                        print(f"VERDICT: FAILED slice={sid} attempts=0 cost=$0.00 error={one_line(f'setup failed ({rc_setup}): {tail}', 120)}")
                    return "FAILED"
        attempt = int(entry.get("attempts") or 0)  # cumulative, for the record
        this_run = 0  # the retry allowance is per invocation
        spent_this_run = 0.0  # and so is the cap: a rerun starts with the full cap again
        result: Dict[str, Any] = {"verdict": "FAILED", "error": "not run", "transient": False, "cost": 0.0, "report": None, "session_id": None, "problems": []}
        while True:
            attempt += 1
            this_run += 1
            with lock:
                entry.update(state="running", attempts=attempt)
                save()
            # The cap is per slice across the attempts of one invocation: a retry
            # runs under what is left, never a fresh cap; a rerun starts over.
            remaining = round(float(budget) - spent_this_run, 4)
            if remaining < WORKER_MIN_BUDGET_USD:
                result = {"verdict": "FAILED", "error": f"budget exhausted: ${spent_this_run:.2f} of ${float(budget):.2f} spent over {this_run - 1} attempt(s) this run",
                          "transient": False, "cost": 0.0, "cost_known": True, "report": None, "session_id": None, "problems": [], "warnings": []}
                with lock:
                    entry["attempts"] = attempt - 1
                attempt -= 1
                break
            result = run_worker_once(str(spec), agent, str(remaining), out_dir, cfg, cwd=cwd, timeout=timeout, slug=sid, attempt=attempt)
            # What the attempt is charged: the CLI's figure when it reported one;
            # otherwise the whole cap it ran under (a timeout, unreadable output:
            # money was spent and nobody said how much), except a transient death,
            # which happens before the work and would otherwise never be retried.
            if result.get("cost_known"):
                charged = float(result.get("cost") or 0.0)
            else:
                charged = 0.0 if result.get("transient") else float(remaining)
                result["cost_assumed"] = charged > 0
            spent_this_run += charged
            with lock:
                entry["cost"] = round(float(entry.get("cost") or 0.0) + charged, 4)
                if result.get("cost_assumed"):
                    entry["cost_assumed"] = True  # part of this figure is a cap charged for an attempt that reported no cost
                entry.update(report=result.get("report") or entry.get("report"), session_id=result.get("session_id") or entry.get("session_id"),
                             error=one_line(result.get("error") or "", 300))
                save()
            if result["verdict"] == "FAILED" and result.get("transient") and this_run <= retries:
                with lock:
                    print(f"RETRY slice={sid} attempt={attempt} in {backoff * (2 ** (this_run - 1)):.0f}s: {one_line(result['error'], 120)}")
                time.sleep(backoff * (2 ** (this_run - 1)))
                continue
            break
        with lock:
            entry.update(state="done" if result["verdict"] == "DONE" else "failed", verdict=result["verdict"])
            if result["verdict"] == "DONE":
                entry["spec_sha"] = spec_digest(spec)
            save()
            line = f"VERDICT: {result['verdict']} slice={sid} attempts={attempt} cost=${entry['cost']:.2f}"
            if result.get("report"):
                line += f" report={result['report']}"
            if cwd:
                line += f" worktree={cwd}"
            if result.get("error"):
                line += f" error={one_line(result['error'], 120)}"
            print(line)
            for pr in result.get("problems") or []:
                print(f"- {one_line(pr, 200)}")
        return str(result["verdict"])

    for sid in skipped:
        print(f"SKIP slice={sid} already DONE (index {index_path})")
    for sid in changed:
        print(f"RERUN slice={sid} was DONE but its spec changed since")
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
    """Print the run index for a plan: supervisor.py runs [PLAN.json|NAME]"""
    root = Path(project_dir or ".").resolve()
    target = next((a for a in args if not a.startswith("--")), None)
    if target and target.endswith(".json"):
        try:
            name = path_label(str(json.loads(Path(target).read_text()).get("name") or Path(target).stem))
        except (OSError, ValueError, AttributeError):
            print(f"cannot read {target}")
            return 2
    else:
        name = path_label(target) if target else None
    base = root / ".supervisor" / "runs"
    dirs = [base / name] if name else sorted(d for d in base.glob("*") if d.is_dir()) if base.exists() else []
    rows = []
    for d in dirs:
        for f in sorted(d.glob("level-*.json")):
            idx = load_index(f)
            if not idx:
                rows.append((d.name, f.stem.replace("level-", ""), "-", "unreadable", "-", "-", "-", str(f), "index file unreadable or malformed"))
                continue
            for sid, e in (idx.get("slices") or {}).items():
                try:
                    cost = f"{float(e.get('cost') or 0):.2f}"
                except (TypeError, ValueError):
                    cost = "?"
                rows.append((d.name, str(idx.get("level")), one_line(sid, 80), str(e.get("state")), str(e.get("verdict")), str(e.get("attempts")), cost, one_line(e.get("report") or "", 200), one_line(e.get("error") or "", 60)))
    if not rows:
        print("no runs yet" + (f" for {name}" if name else ""))
        return 0
    print("| plan | level | slice | state | verdict | attempts | USD | report | error |")
    print("|---|---:|---|---|---|---:|---:|---|---|")
    for r in rows:
        print("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
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
HOOK_EVENTS = frozenset({"session-start", "user-prompt", "pre-tool-use", "post-tool-use", "subagent-stop", "session-end"})


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
    if event == "spec":
        return cmd_spec(args, cfg)
    if event == "runs":
        return cmd_runs(args, project_dir)
    if event not in HOOK_EVENTS:
        print(f"supervisor: unknown event {event}", file=sys.stderr)
        return 2

    try:
        hook = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except ValueError:
        hook = {}
    sid = clean_label(str(hook.get("session_id") or "unknown"))
    if os.environ.get("SUPERVISOR_DEBUG"):
        debug_dump(event, hook)
    lock = session_lock(sid)
    ledger = Ledger(sid, Pricing.load())
    handlers = {
        "session-start": lambda: h_session_start(hook, cfg, ledger),
        "user-prompt": lambda: h_user_prompt(hook, cfg, ledger),
        "pre-tool-use": lambda: h_pre_tool_use(hook, cfg, ledger, project_dir),
        "post-tool-use": lambda: h_post_tool_use(hook, cfg, ledger, project_dir),
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
        print(f"supervisor: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
