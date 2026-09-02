# Documentation as a failure surface

Checks 23 to 25, plus the scanner's endpoint drift. Documentation is
executed by readers; wrong text costs more than no text.

## Contents

- docs-endpoint-drift (scanner)
23. Wrong troubleshooting symptoms
24. Upstream examples that do not work
25. Claims the API does not support

## docs-endpoint-drift

The scanner compares paths in the OpenAPI document with endpoint mentions
in the markdown. Documented-not-in-spec is a reader sent to a route that
does not exist; in-spec-not-documented is a route nobody explained. Both
are rows for the auditor, who decides which side is wrong.

## 23. Wrong troubleshooting symptoms

**What happened.** A troubleshooting section was written from inference
and never revisited when the code path underneath it changed. It sent a
reader hunting for a state that could not occur.

**Rule.** Every symptom in a troubleshooting section maps to a code path
that produces it, named in a comment beside the text or in a test that
provokes it. When the code path goes, the text goes.

## 24. Upstream examples that do not work

**What happened.** A provider's own guide showed a request format its
gateway rejected.

**Rule.** Samples are executed in CI against the sandbox, not read. A
sample that cannot be executed is labelled as untested in the text.

## 25. Claims the API does not support

**What happened.** A sample asserted a business model the API never
describes. Readers built to it.

**Rule.** Every claim about what the API does traces to a line in its
specification or reference documentation, cited. The auditor's question
for any unsourced claim is "where does the provider say this".
