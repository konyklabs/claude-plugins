# Brief: <slug>

## Task
One sentence: the observable outcome, in the user's words.

## Definition of done
- [ ] a statement a script or a glance can confirm: a command, a count, a file, a state
- [ ] `<command>` exits 0
- [ ] no file outside the slices' file lists is modified (`git status --short`)

## Evidence
```
$ <the command whose output proves the definition of done>
```

## Out of scope
- what looks adjacent and is not wanted

## Decisions already made
- decision — reason, one line   (or: none)

## Assumptions
- what the interview did not cover, and what was assumed instead   (or: none)

## Procedure
- run /supervisor:triage first and show the table before any work
- /supervisor:decompose when the work touches more than about five files
- /supervisor:delegate per slice; workers: supervisor:implementer (or py-testing:test-implementer for tests); supervisor:reviewer on every slice that changes behaviour
- plugin agents by name, nothing implemented inline, no other agent type
- a BLOCKED or PARTIAL report is answered by the conductor and re-delegated
