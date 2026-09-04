# Evals

Cases for `claude plugin eval`, in the early-access layout (`<case>/prompt.md`
with frontmatter, `<case>/graders/*.md`). Written 2026-09-04 against the
embedded early-access reference; the feature is not enabled on the authoring
account, so these cases have been validated for shape only and **have not
been run**. When the feature is available:

```
claude plugin eval plugins/signoff --runs 1 --max-cost-usd 5 --no-publish
```

Grader intent is stated in each grader file so it can be checked by hand.

Every prompt assumes the working directory is a copy of `tests/fixture-app/`
with its `.qa/map.json` and `.qa/rules.json` already present, the way the
fixture ships them (see `tests/fixture-app/README.md`). Make that copy
before running these, the same way `tests/test_fixture_app_gaps.py` does,
rather than pointing an eval run at the repository's own fixture directory.
