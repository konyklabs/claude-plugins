# Debug and observability surfaces

Checks 6 to 9. A debug surface is a feature that ships; the checks are
about what it leaks and who can reach it.

## Contents

6. redaction-at-publish
7. denylist versus allowlist redaction
8. debug-endpoint-exposure
9. html-sinks

## 6. redaction-at-publish

**What happened.** A live traffic inspector streamed every upstream request
and response to the browser over server-sent events, with redaction. The
redaction ran in the client, at render. The stream kept a replay buffer so
a new subscriber saw recent history. The buffer held the unredacted
events, and every new subscriber received them.

**Check.** The scanner finds stream publishers (`text/event-stream`,
`EventSource`, `StreamingResponse`, websockets) and reports, per file:
whether a redaction word appears in the publisher, whether a replay buffer
word appears, and which of allowlist or denylist vocabulary is present.
The judgment is the auditor's; the rule is one sentence: **redact where
the event is produced, before it enters any buffer.** Scrubbing at render
leaves the leak fully intact for anyone who reads the stream directly.

**Test.** Subscribe twice, the second time after a request carrying a
canary header value; assert the second subscriber's replayed history does
not contain the canary.

## 7. denylist versus allowlist

A key-name denylist (`authorization`, `cookie`, `x-api-key`) fails open on
the next field the upstream adds. An allowlist of fields to show fails
closed. Either can be right: where the response data is the point of the
panel, a denylist is a deliberate trade. It must be stated in the code, at
the redaction site, as a decision with its reason; the scanner reports
which vocabulary it found so the auditor can ask for the statement.

## 8. debug-endpoint-exposure

**What happened.** A debug route was protected by checking the bind
address at startup. Binding `0.0.0.0` later, for a container, exposed it.

**Check.** The loopback check is performed **per request** on the peer
address, so the bind address cannot change the answer:

```python
def _loopback_only(request):
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1"):
        raise HTTPException(404)
```

Plus a per-process token minted at startup and required on the route.
Be exact in the guidance: the token is a tripwire, not authentication.
Anything that can read the page can read the token; its job is to make
accidental exposure fail loudly, not to keep an attacker out.

**When it is wrong.** Behind a reverse proxy the peer address is the proxy.
Then the route must not exist in that deployment at all; the scanner's
`X-Forwarded-For` mention in a loopback check is a finding, not a pass.

## 9. html-sinks

**What happened.** A hand review of the browser client looked for
`innerHTML`. A test over the static directory found an
`insertAdjacentHTML` on its first run.

**Check.** The scanner greps every script and markup file (excluding
vendored and minified) for the sink list: `innerHTML =`, `outerHTML =`,
`insertAdjacentHTML(`, `document.write(`, `new DOMParser(`,
`createContextualFragment(`, `eval(`, `new Function(`. Every hit is a
`fail` with `path:line`.

**Fix.** `textContent` for text, `createElement` plus attributes for
structure, a template element cloned for markup that is genuinely
constant. Keep the test in the suite:

```python
SINKS = re.compile(r"\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML\(|document\.write\(|new DOMParser\(|createContextualFragment\(|\beval\(|new Function\(")

def test_static_has_no_html_string_sinks():
    hits = [(p, i) for p in Path("static").rglob("*.js") for i, line in enumerate(p.read_text().splitlines(), 1) if SINKS.search(line)]
    assert hits == []
```

**When it is wrong.** A sink fed a constant is safe. The finding is the
sink; the auditor decides the source. The fix is still usually cheaper
than the argument.
