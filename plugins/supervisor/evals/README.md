# Evals

Cases for `claude plugin eval` (released in Claude Code v2.1.269, 2026-09-11;
docs: code.claude.com/docs/en/plugin-evals). Layout: `<case>/prompt.md` with
frontmatter, `<case>/graders/*.md`. Written 2026-09-02 against the
early-access reference and first run on the released tool on 2026-09-27
(claude-plugins branch for konyklabs/roadmap#148), which changed two things:
`llm` graders take `focus`, not `target`, and a tool a grader depends on must
be both in the case's `allowed_tools` and granted by the operator. From the
repository root:

```
claude plugin eval plugins/supervisor --runs 1 --max-cost-usd 5 --no-publish --trust-plugin --allow-tools Bash Write
```

`--case` takes one glob (the last one wins); use `--tag` to select several
cases. Every run is a model call on the account. Results land in
`evals/results/` (gitignored).

Grader intent is stated in each grader file so it can be checked by hand.

`brief-writes-file` allows 12 turns and 300 seconds where the other cases
allow 8 and 240: an interview has AskUserQuestion round-trips the one-shot
cases do not.
