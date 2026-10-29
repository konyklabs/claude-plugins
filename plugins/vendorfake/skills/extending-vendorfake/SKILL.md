---
name: extending-vendorfake
description: What to do when vendorfake lacks a route, a field, an event or a vendor the consumer needs — the route-by-route fit check with scripts/fit_check.py over `vendorfake routes --json`, confirming the gap from the near-miss log, filing it in konyklabs/vendorfake with the consumer described generically, building it in the fake's own conventions (DOCUMENTED or JUDGMENT provenance with a working citation, red first, the boundary rules, a conformance id from the manifest, regenerated reference docs, the prose-ratio gate), and consuming the branch before its tag through VENDORFAKE_REF. Use when vendorfake answers 404 or a Vendorfake-Near-Miss, when a fit check shows a missing row, when contributing a route or vendor to vendorfake, or when a consumer needs a vendorfake change before it is released.
---

# Extending vendorfake

A gap is first confirmed, then filed, then built in the fake; it is never stubbed
around silently in the consumer's e2e tier (stubs belong in the in-process tier,
where they are visible). vendorfake's own `AGENTS.md` is the contributor contract;
this skill is the consumer-side half plus the parts of AGENTS.md a one-route change
needs. Argument-level facts: `vendorfake explain --json route <operation_id> --vendor V`
when installed, else the pages in Sources.

## 1. Confirm the gap

```
vendorfake routes --vendor clover --json > /tmp/routes.json
python3 "${CLAUDE_SKILL_DIR}/scripts/fit_check.py" --routes /tmp/routes.json --calls calls.txt [--mount clover] [--json]
```

`calls.txt` is one `METHOD /path` per line, inventoried from the consumer's production
code (not its docs); `#` comments and blank lines are ignored. Verdicts: `fit` (with
the operation id), `method` (the path is served under other methods, listed),
`missing`. Exit 1 on any non-fit, 2 on a malformed line. `--mount V` only for a
multi-vendor process. Then confirm at run time: `GET /__unit/requests?unmatched=true`,
`GET /__unit/requests/unmatched/near-misses`, the `Vendorfake-Near-Miss` header.
Known non-fits: Lightspeed in vendorfake is Retail X-Series, and the Restaurant
K-Series API is a new vendor package, not a gap; Square's SDK `base_url` seam is
consumer-side; check the next tag first (`GET /v2/channels` arrived in 0.8.0).

## 2. File it

An issue in github.com/konyklabs/vendorfake (public) carrying: the route with method
and path template, the vendor's documentation URL, the fields the consumer reads, the
statuses it branches on, and the consumer described generically ("a consumer that
syncs item availability"). Never a company, codename or internal system name.

## 3. Build it in the fake's conventions

- `uv sync --all-groups && tools/self-test.sh` is the one command; its summary table
  is the evidence.
- Layout: the route lives in `src/vendorfake/<vendor>/`; `core/` imports no web
  framework; no vendor slug under `core/` or `conformance/`; the environment is read
  only in `registry.ambient_env()`.
- Label every reproduced behaviour at the site: `DOCUMENTED` (cite the vendor page and
  check the URL works) or `JUDGMENT` (say why).
- Red first: the failing vendor test under `tests/unit/<vendor>/` before the handler.
- A new conformance contract takes the next free C-id AGENTS.md names (C38 at 0.8.0)
  and updates `src/vendorfake/conformance/manifest.json` in the same change.
- Regenerate `docs/reference` with `uv run python tools/gen_reference.py`, or the
  `docs` row is red.
- The `prose ratio` row (`tools/prose_ratio.py src --max-total 15`) sat at exactly
  15.0 at 0.8.0: a docstring that grows needs a trim elsewhere.
- A CHANGELOG bullet under `## Unreleased`: `* **vendor:** ... (konyklabs/roadmap#N)`.
- `fidelity fetch (toast)` and `fidelity pin (toast)` need the network; they SKIP
  without it and can go red for upstream reasons unrelated to the change.

## 4. Consume it before the tag

Set the consumer's build arg to `VENDORFAKE_REF=refs/heads/<branch>` or a sha, and
rebuild with `docker compose build --no-cache` (the layer caches on the ARG string).
`__version__` then reports the previous release (install.md), so never branch on it.
Move back to the tag the day it ships.

## Traps

- A citation with no working URL is worse than none.
- A test that asserts prose, or would pass against a stub echoing its own fixture, is deleted under docs/testing.md's rule.
- A behaviour that differs between bindings is an `xfail(strict=True)` case in `tests/parity/`.
- Vendor names are the one allowed real-company exception; everything else about the consumer stays generic.

## Sources

vendorfake at tag v0.8.0 (0145227): AGENTS.md, docs/testing.md, docs/start/install.md,
docs/concepts/unit.md, tools/self-test.sh, tools/prose_ratio.py; fetched 2026-10-29.
The docs are silent on how a consumer consumes an unreleased branch (install.md
covers commit pins; the archive-by-branch form was verified by install). Details in
`references/`.
