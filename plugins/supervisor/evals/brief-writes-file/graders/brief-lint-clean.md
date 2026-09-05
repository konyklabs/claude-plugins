---
type: llm
target: trace
---
Score 1 if the assistant asks at most five questions, writes .supervisor/brief.md with the headings Task, Definition of done, Evidence, Out of scope, Assumptions and Procedure, runs `supervisor.py brief check` on it through Bash, and the last check printed OK. Score 0 if it starts triage or implementation in the same turn, asks more than five questions, writes the brief without running the check, or ends on a NONCOMPLIANT check.
