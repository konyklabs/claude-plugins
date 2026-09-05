# Changelog

## [2.1.0](https://github.com/konyklabs/claude-plugins/compare/v2.0.0...v2.1.0) (2026-09-05)


### Features

* **signoff:** plugin skeleton, the tile, cases and report scripts, a fixture app with known gaps, evals (konyklabs/roadmap[#120](https://github.com/konyklabs/claude-plugins/issues/120)) ([#16](https://github.com/konyklabs/claude-plugins/issues/16)) ([9975800](https://github.com/konyklabs/claude-plugins/commit/99758003b932fc95a1f1cf63e1aa65e583d4df89))
* **supervisor:** per-session budget, spend reset, and the budget CLI passes a closed gate (konyklabs/roadmap[#128](https://github.com/konyklabs/claude-plugins/issues/128)) ([#20](https://github.com/konyklabs/claude-plugins/issues/20)) ([839df83](https://github.com/konyklabs/claude-plugins/commit/839df83e17c3d004cbf10a5a4ab3445b670659bc))

## [2.0.0](https://github.com/konyklabs/claude-plugins/compare/v1.1.0...v2.0.0) (2026-09-05)


### ⚠ BREAKING CHANGES

* **supervisor:** rename governor to supervisor; dormant until armed per session (konyklabs/roadmap#126) ([#18](https://github.com/konyklabs/claude-plugins/issues/18))

### Features

* **governor:** /governor:start chains brief, triage and the cut with two stops; budget by profile with a ceiling (konyklabs/roadmap[#125](https://github.com/konyklabs/claude-plugins/issues/125)) ([#17](https://github.com/konyklabs/claude-plugins/issues/17)) ([75688a9](https://github.com/konyklabs/claude-plugins/commit/75688a997abd982b512058a2d4a56919a75eb402))
* **governor:** name dead workers, advise retry or tier switch, guard limit-hit models, route bare spawns to worker (konyklabs/roadmap[#115](https://github.com/konyklabs/claude-plugins/issues/115)) ([#14](https://github.com/konyklabs/claude-plugins/issues/14)) ([1d4e015](https://github.com/konyklabs/claude-plugins/commit/1d4e015c5ddc50b2ae166c3d650ff48d47be9b43))
* **supervisor:** rename governor to supervisor; dormant until armed per session (konyklabs/roadmap[#126](https://github.com/konyklabs/claude-plugins/issues/126)) ([#18](https://github.com/konyklabs/claude-plugins/issues/18)) ([b6e5f47](https://github.com/konyklabs/claude-plugins/commit/b6e5f4730504582e34a1be4ffe14205542052556))

## [1.1.0](https://github.com/konyklabs/claude-plugins/compare/v1.0.0...v1.1.0) (2026-09-03)


### Features

* **governor:** add the brief skill and the operator playbook (konyklabs/roadmap[#78](https://github.com/konyklabs/claude-plugins/issues/78), konyklabs/roadmap[#77](https://github.com/konyklabs/claude-plugins/issues/77)) ([#8](https://github.com/konyklabs/claude-plugins/issues/8)) ([eb8f269](https://github.com/konyklabs/claude-plugins/commit/eb8f269d51f5f325aabdeba33360b189d815c39a))
* **governor:** explore mode and /governor:explore for loosely defined work (konyklabs/roadmap[#82](https://github.com/konyklabs/claude-plugins/issues/82)) ([#10](https://github.com/konyklabs/claude-plugins/issues/10)) ([abbdaba](https://github.com/konyklabs/claude-plugins/commit/abbdaba88f132cfeca0690c37681e527fe314145))
* **governor:** run-level, supervised headless workers with retry, worktrees and resume (konyklabs/roadmap[#97](https://github.com/konyklabs/claude-plugins/issues/97)) ([#11](https://github.com/konyklabs/claude-plugins/issues/11)) ([1424a05](https://github.com/konyklabs/claude-plugins/commit/1424a05dc2353af25e1b2737a00c167f7e6f75a3))
* **governor:** spec check before dispatch, worker spend in the readout, worktree setup (konyklabs/roadmap[#100](https://github.com/konyklabs/claude-plugins/issues/100)) ([#12](https://github.com/konyklabs/claude-plugins/issues/12)) ([fa97857](https://github.com/konyklabs/claude-plugins/commit/fa97857d7dad3eec005c057a07bfc3cb6bc778fc))

## 1.0.0 (2026-09-02)


### Features

* governor harness and py-testing skills, first set (konyklabs/roadmap[#60](https://github.com/konyklabs/claude-plugins/issues/60)) ([aa5c96b](https://github.com/konyklabs/claude-plugins/commit/aa5c96b4d5b18b17c8f56643eb5f6d219010606d))
* **prod-readiness:** production-readiness and security-scanning plugin (konyklabs/roadmap[#61](https://github.com/konyklabs/claude-plugins/issues/61)) ([3f017ed](https://github.com/konyklabs/claude-plugins/commit/3f017edddd36d42660e92a6da0ae571584467586))
