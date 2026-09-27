---
type: regex
target: last_message
pattern: "^(?=[\\s\\S]*budget)[\\s\\S]*\\$\\d+(\\.\\d+)?[^$]{0,120}\\$\\d+(\\.\\d+)?"
flags: i
---
The answer states the expensive-tier spend and the budget as two dollar
figures within a sentence ("$0.00 of $15.00", "$0.00 of a $15.00 budget")
and names the budget (the lookahead), so two unrelated amounts do not pass. The supervisor readout supplies both every turn once the
session is armed, so answering from it is correct; running `supervisor.py
budget show` through Bash is an acceptable path, not a required one (the
first real run on 2026-09-27 answered from the readout in one turn, and a
`tool_used: Bash` grader scored that correct answer 0).
