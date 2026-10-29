# Evals

Cases for `claude plugin eval` (released in Claude Code v2.1.269, 2026-09-11;
docs: code.claude.com/docs/en/plugin-evals). Layout: `<case>/prompt.md` with
frontmatter, `<case>/graders/*.md`, and for a scaffolded case a `case.yaml`
naming its `scaffold_script`. Written 2026-10-29 against the released tool
(CLI 2.1.312), with the schema facts the supervisor evals found on their first
run: `llm` graders take `focus`, not `target`, and a tool a grader depends on
must be both in the case's `allowed_tools` and granted by the operator. From
the repository root:

```
claude plugin eval plugins/vendorfake --runs 1 --max-cost-usd 5 --no-publish --trust-plugin --scaffold --allow-tools Bash Write Edit
```

`--scaffold` because `adopt-clover-compose` seeds a consumer first:
`fixture.sh` writes a small generic service (a compose file with one `app`
service and empty Clover settings, a Dockerfile, `app/settings.py`,
`app/clover_client.py`, an empty `tests/e2e/`) and nothing else: no git, no
network, nothing executed. The other two cases start in an empty workspace.
`--tag adopting`, `--tag testing` or `--tag extending` selects one case. Every
run is a model call on the account. Results land in `evals/results/`
(gitignored by the root `.gitignore`).

Each case carries one `tool_used: Skill` grader naming its skill: under the
default with/without ablation that grader is with-only, a plugin-fired
indicator rather than part of the score. The scored graders read the
workspace's `compose.yaml` (adopting), the trace (the names a correct test
must use) or the last message (the procedure), and grader intent is stated in
each grader file so it can be checked by hand.

`adopt-clover-compose` allows 20 turns and 600 seconds: it reads a scaffold,
writes two files and answers. `test-clover-modifier-webhook` allows 12 and
300; `extend-missing-route`, a one-answer case, 8 and 240.
