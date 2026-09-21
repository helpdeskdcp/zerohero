# Engine Change Control Log

Process doc + append-only log for any future change to a PRODUCTION engine
(app/engines/scalp_strategy.py, app/engines/state_classifier.py, or any
live/paper decision path) proposed as a result of reverse-engineering
research. No entry here authorizes a change by itself — each still needs
its own explicit human approval before merge, per this project's standing
rule (scalp_strategy.py stays untouched without explicit approval).

## Required fields per entry

- **old_rule** — the exact current behavior/formula being changed
- **new_rule** — the exact proposed replacement
- **reason** — what evidence motivates this (link the research report)
- **dataset_version** — which `research_events.db` snapshot / event count this was tested against
- **test_results** — unit/integration test outcome
- **walk_forward_results** — walk-forward validation outcome (chronological splits only)
- **out_of_sample_results** — held-out period outcome
- **rollback_decision** — KEEP_BASELINE or ACCEPT_CHANGE, with the deciding number

A change is accepted only if out-of-sample results are statistically
defensible improvement over baseline WITHOUT materially increasing false
positives. If not: **KEEP BASELINE**.

---

## Log

### Entry 1 — 2026-09-21

- **old_rule**: n/a
- **new_rule**: n/a
- **reason**: n/a
- **dataset_version**: research_events.db, n=5 (screenshot-derived, see PHASE0_PHASE1_REPORT.md)
- **test_results**: n/a
- **walk_forward_results**: n/a
- **out_of_sample_results**: n/a
- **rollback_decision**: **KEEP_BASELINE** — no production engine change has been made from
  reverse-engineering research to date. The current dataset (n=5) is far below any
  threshold (this project's own track record: n≥50-100) that would make a change
  statistically defensible. This entry exists to record that explicitly, not to propose
  a change.
