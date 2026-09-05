# Evals

Cases for `claude plugin eval`, in the early-access layout (`<case>/prompt.md`
with frontmatter, `<case>/graders/*.md`). Written 2026-09-02 against the
embedded early-access reference; the feature is not enabled on the authoring
account, so these cases have been validated for shape only and **have not
been run**. When the feature is available:

```
claude plugin eval plugins/supervisor --runs 1 --max-cost-usd 5 --no-publish
```

Grader intent is stated in each grader file so it can be checked by hand.

`brief-writes-file` allows 12 turns and 300 seconds where the other cases
allow 8 and 240: an interview has AskUserQuestion round-trips the one-shot
cases do not.
