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

`report-with-diff` needs one more thing in that copy: the previous sign-off's
tiles file its prompt names. Build it by running the two tiling scripts once
in the copy, keeping the result as the previous file, then deleting one tile
from it so the diff is not empty. From the copy, with `$S` the path to
`plugins/signoff/skills`:

```
python3 $S/tiling-coverage/scripts/tests.py --stack pytest e2e --out .qa/tests.json
python3 $S/tiling-coverage/scripts/tile.py --map .qa/map.json --rules .qa/rules.json \
  --tests .qa/tests.json --out .qa/tiles.json
cp .qa/tiles.json .qa/tiles-previous.json
python3 -c "import json; p='.qa/tiles-previous.json'; d=json.load(open(p)); \
  d['tiles']=d['tiles'][1:]; json.dump(d, open(p,'w'), indent=2)"
```

The last line drops one tile, so the report's diff has a `New` entry to
find; the prompt itself is unchanged by any of this.
