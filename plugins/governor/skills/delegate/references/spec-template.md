# Spec: <slug>

**Goal.** One sentence; the observable outcome when this is done.

## Files

Paths and values below are exact; the conductor resolved them.

Change:
- `path/to/file.py` — what changes, five words

Leave alone (adjacent, not in scope):
- `path/to/other.py`

## Definition of done

- [ ] checkable statement
- [ ] `pytest -q tests/<dir>` exits 0
- [ ] no file outside the Change list is modified

## Tests to run

```
$ <exact command>
```

## Decisions already made (do not re-open)

- decision — reason, one line

## Out of scope

- thing that looks adjacent and is not wanted

## Report

Return the report format your agent definition requires: `## Result` with
DONE, PARTIAL or BLOCKED; `## Changed files`; `## Evidence` with the command
on a `$ ` line and its output. Forty lines maximum.
