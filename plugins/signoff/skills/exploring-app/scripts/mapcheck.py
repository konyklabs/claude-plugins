#!/usr/bin/env python3
"""Validate an application map before anything downstream trusts it.

``.qa/map.json`` is written by the explore step and read by every step after
it, so a dangling screen reference or an invented action kind becomes a wrong
tile rather than an error. This checks the map's shape: required fields,
unique ids, every reference to a screen resolving, every role known, every
action kind in the allowed set.

``plugins/signoff/formats.md`` is the only home of the format; this script
does not restate it.

Usage:
    mapcheck.py .qa/map.json

Prints the counts, then one line per problem as `path:line rule: reason`.
Exit 0 when the map is clean, 1 when it has a problem, 2 when it cannot be
read at all.

Standard library only, no network.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Tuple

# formats.md: the four kinds an action may have. `mutate` is the destructive
# one the explorer records but never performs unless the allowlist names it,
# so an action mistyped as anything else would be performed - which is why an
# unknown kind is a problem and not a warning.
ACTION_KINDS = ("navigate", "submit", "toggle", "mutate")

# formats.md's map: the top-level keys, and the fields a screen and a flow
# each carry. `actions`, `forms`, `states` and `links` are optional - a screen
# with none of them is a real screen - but the five below identify it.
TOP_LEVEL_REQUIRED = ("base_url", "explored_at", "roles", "screens", "flows")
SCREEN_REQUIRED = ("id", "area", "path", "title", "roles")
FLOW_REQUIRED = ("id", "area", "name", "role", "steps")
ACTION_REQUIRED = ("id", "kind")

# formats.md: the `states` prefix tile.py turns into an error tile. Counted
# separately because those tiles exist only if the prefix is spelled this way.
ERROR_STATE_PREFIX = "error:"

# `json` does not keep line numbers, so every finding points at the file
# itself. The `path:line rule` shape is kept so findings from every script in
# this plugin read and sort the same way.
NO_LINE = 0


def _finding(path: str, rule: str, reason: str) -> str:
    return "%s:%d %s: %s" % (path, NO_LINE, rule, reason)


def check(document: Any, path: str) -> Tuple[List[str], Dict[str, Any]]:
    """(problems, counts) for one parsed map."""
    problems: List[str] = []
    counts: Dict[str, Any] = {"screens": 0, "flows": 0, "roles": 0,
                              "actions": {kind: 0 for kind in ACTION_KINDS},
                              "actions_other": 0, "error_states": 0}
    if not isinstance(document, dict):
        return [_finding(path, "malformed", "the map is not a JSON object")], counts

    for field in TOP_LEVEL_REQUIRED:
        if field not in document:
            problems.append(_finding(path, "missing-field", "the map has no %s" % field))

    roles = document.get("roles")
    if not isinstance(roles, list):
        roles = []
        if "roles" in document:
            problems.append(_finding(path, "malformed", "roles is not a list"))
    known_roles = {str(role) for role in roles}
    counts["roles"] = len(known_roles)

    screens = document.get("screens")
    if not isinstance(screens, list):
        screens = []
        if "screens" in document:
            problems.append(_finding(path, "malformed", "screens is not a list"))
    flows = document.get("flows")
    if not isinstance(flows, list):
        flows = []
        if "flows" in document:
            problems.append(_finding(path, "malformed", "flows is not a list"))

    screen_ids: List[str] = []
    for screen in screens:
        if not isinstance(screen, dict):
            problems.append(_finding(path, "malformed", "a screen is not an object"))
            continue
        counts["screens"] += 1
        for field in SCREEN_REQUIRED:
            if field not in screen:
                problems.append(_finding(path, "missing-field", "screen %s has no %s"
                                         % (screen.get("id", "<unnamed>"), field)))
        screen_id = str(screen.get("id", "<unnamed>"))
        if screen_id in screen_ids:
            problems.append(_finding(path, "duplicate-id", "screen %s is defined twice" % screen_id))
        screen_ids.append(screen_id)
        for state in screen.get("states") or []:
            if str(state).startswith(ERROR_STATE_PREFIX):
                counts["error_states"] += 1
    known_screens = set(screen_ids)

    flow_ids: List[str] = []
    for flow in flows:
        if not isinstance(flow, dict):
            problems.append(_finding(path, "malformed", "a flow is not an object"))
            continue
        counts["flows"] += 1
        for field in FLOW_REQUIRED:
            if field not in flow:
                problems.append(_finding(path, "missing-field", "flow %s has no %s"
                                         % (flow.get("id", "<unnamed>"), field)))
        flow_id = str(flow.get("id", "<unnamed>"))
        if flow_id in flow_ids:
            problems.append(_finding(path, "duplicate-id", "flow %s is defined twice" % flow_id))
        flow_ids.append(flow_id)

    # References. Every one of them names a screen, so a typo here is a step
    # nothing can walk and a tile nothing can claim.
    for screen in screens:
        if not isinstance(screen, dict):
            continue
        screen_id = str(screen.get("id", "<unnamed>"))
        for role in screen.get("roles") or []:
            if str(role) not in known_roles:
                problems.append(_finding(path, "unknown-role", "screen %s names role %s, which is not in roles"
                                         % (screen_id, role)))
        for action in screen.get("actions") or []:
            if not isinstance(action, dict):
                problems.append(_finding(path, "malformed", "an action of screen %s is not an object" % screen_id))
                continue
            for field in ACTION_REQUIRED:
                if field not in action:
                    problems.append(_finding(path, "missing-field", "action %s of screen %s has no %s"
                                             % (action.get("id", "<unnamed>"), screen_id, field)))
            kind = str(action.get("kind", ""))
            if kind in ACTION_KINDS:
                counts["actions"][kind] += 1
            else:
                counts["actions_other"] += 1
                problems.append(_finding(path, "unknown-action-kind", "action %s of screen %s has kind %s, not one of %s"
                                         % (action.get("id", "<unnamed>"), screen_id, kind or "<none>",
                                            ", ".join(ACTION_KINDS))))
            target = action.get("to")
            if target is not None and str(target) not in known_screens:
                problems.append(_finding(path, "unknown-screen", "action %s of screen %s goes to %s, which is not a screen"
                                         % (action.get("id", "<unnamed>"), screen_id, target)))
        for link in screen.get("links") or []:
            if str(link) not in known_screens:
                problems.append(_finding(path, "unknown-screen", "screen %s links to %s, which is not a screen"
                                         % (screen_id, link)))

    for flow in flows:
        if not isinstance(flow, dict):
            continue
        flow_id = str(flow.get("id", "<unnamed>"))
        role = flow.get("role")
        if role is not None and str(role) not in known_roles:
            problems.append(_finding(path, "unknown-role", "flow %s runs as role %s, which is not in roles"
                                     % (flow_id, role)))
        for step in flow.get("steps") or []:
            if str(step) not in known_screens:
                problems.append(_finding(path, "unknown-screen", "flow %s steps through %s, which is not a screen"
                                         % (flow_id, step)))
    return problems, counts


def render_counts(counts: Dict[str, Any]) -> str:
    actions = counts["actions"]
    total = sum(actions.values()) + counts["actions_other"]
    by_kind = ", ".join("%s %d" % (kind, actions[kind]) for kind in ACTION_KINDS)
    if counts["actions_other"]:
        by_kind += ", unknown %d" % counts["actions_other"]
    return "\n".join([
        "screens: %d" % counts["screens"],
        "flows: %d" % counts["flows"],
        "roles: %d" % counts["roles"],
        "actions: %d (%s)" % (total, by_kind),
        "error states: %d" % counts["error_states"],
    ]) + "\n"


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("map_path", help="the map to check (usually .qa/map.json)")
    args = parser.parse_args(argv)

    try:
        with open(args.map_path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as error:
        print("mapcheck.py: %s" % _finding(args.map_path, "unreadable", str(error)), file=sys.stderr)
        return 2

    problems, counts = check(document, args.map_path)
    sys.stdout.write(render_counts(counts))
    for problem in problems:
        print(problem)
    if problems:
        print("%d problem%s" % (len(problems), "" if len(problems) == 1 else "s"))
        return 1
    print("map ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
