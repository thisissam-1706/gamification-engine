# Week 6 / M4 / Step 5 — traceability matrix and README for M4

Status: **Prompt 2 of Step 5 — docs updated; no code or test changed.** (Prompt 1 is in `week6-m4-step5-at17-docs.md`.)
Git rule in force: no state-changing git commands (only `git diff`/`git status` were used).

## 1. Changes made

`docs/traceability-matrix.md` and `README.md` only. No source, test or other doc touched.

## 2. Before / after of every changed line

### docs/traceability-matrix.md

1. Top changelog — added before the first `> **Changelog (2026-10-26)…` line:
   `> **Changelog (2026-11-02):** M4 documented as implemented for synthetic fixtures. AT-17 written and passing (...); the M4 row status, the M1 status ... and the "Reused, configured, new" table ... were updated to match. No other acceptance criteria, numbers or thresholds changed.`
2. M1 row, Status:
   - before: `partial (lesson_completed only; ID binding, late window, rule selection tested)`
   - after:  `partial (lesson_completed and checkpoint_attempted ingested; ID binding, late window, rule selection tested)`
3. M4 row (Owner, Peer reviewer, Target week unchanged):
   - before: Acceptance tests `AT-17 (to be written)`; Status `not started`
   - after:  Acceptance tests `AT-17`; Status `implemented for synthetic fixtures: checkpoint_attempted ingestion, rule-based remediation policy with stored decisions, remediation_v1 quest (remediation completions only), deadline expiry sweep; policy and sweep invoked, not scheduled; no notification hook`
4. "Reused, configured, new" — four added rows (none removed):
   - configured: `Remediation policy settings (14-day frequency window, 7-day quest deadline, quest id and definition version, policy version)` — `RemediationConfig` in `src/interventions/config.py`
   - new: `Remediation policy and runners` — `decide_remediation`, `RemediationDecision` in `src/interventions/policy.py`; `run_remediation`, `run_quest_expiry` in `src/interventions/service.py`
   - new: `Quest definitions (remediation_v1)` — `quest_definition_for`, `QuestDefinition` in `src/state_machine/quest_definitions.py` (rule-set quests fall back to `rulesets.json`)
   (the configured row sits before "Ingestion late window and clock"; the two new rows sit before "Mastery metric").
5. Acceptance test 17:
   - before: `17. **Learning-Oriented Remediation Quest (M4) (test to be written):** Given learner L1 failed their first eligible checkpoint attempt and has received no remediation quest in the frequency window, when the policy runs, then a remediation quest is assigned with a deadline. If L1 already received one inside the window, the outcome is an explicit no-action decision, and an unfinished quest expires at its deadline.`
   - after: your supplied wording, with **one adjustment**: "received no remediation quest assignment" became "received no remediation quest assignment (an assign decision, which assigns the quest in the same save)". See §3.

### README.md

1. Mermaid diagram — added nodes (after `Notifications[...]`):
   `Intervention["Intervention policy (remediation)<br/>stored decisions + remediation quest"]` and `Sweep["Quest expiry sweep"]`; added four dashed (invoked) edges (after the `IngestionEvidence ... Abuse` edge):
   `Aggregate -. invoked, reads attempts and prior decisions .-> Intervention`, `Intervention -. decision + quest, one save .-> Aggregate`, `Aggregate -. invoked, reads overdue quests .-> Sweep`, `Sweep -. QuestExpired at deadline .-> Aggregate`.
   The prose paragraph under the diagram is unchanged (see §4).
2. ADR list: added `- [ADR-0007: Remediation Policy with Stored Decisions and a Quest Definition](docs/adr/0007-remediation-policy-and-quest.md)` after ADR-0006. **The file does not exist yet** (next prompt); the link is dangling until then, and the next prompt must use exactly this filename (or this link must change).
3. Milestone Status, M1:
   - before: `☐ (partial: lesson_completed only; ID binding, late window, rule selection tested)`
   - after:  `☐ (partial: lesson_completed and checkpoint_attempted ingested; ID binding, late window, rule selection tested)`
4. Milestone Status, M4:
   - before: `☐`
   - after:  `☐ (implemented for synthetic fixtures: ... no notification hook)` — same text as the matrix. I kept `☐`, the convention for M3/M5/M6/M7 (slice implemented, not complete); `☑` is used only for M2/M8.
5. Planned Placeholder Tests: added `| test_learning_remediation | M4 | passing (AT-17; synthetic fixtures; policy and sweep invoked, not scheduled) |` after `test_point_farming`.

## 3. Claims checked against the code

- "earliest by occurred_at, then attempt_id" — `Learner._record_checkpoint_attempt` orders by `(occurred_at, attempt_id)`. ✔
- 14 days / 7 days / `remediation_v1` / `remediated_within_window` — `RemediationConfig`, `decide_remediation`. ✔ ("less than 14 days earlier" = strictly inside; exactly 14 days is outside, tested.)
- "received no remediation quest assignment in the previous 14 days" — **the code measures prior `assign` decisions' `decided_at`, not quest assignments.** For decisions made by `run_remediation` the two coincide (decision and quest are saved together), but a quest assigned by hand or another path would not block. Hence my parenthetical. If you prefer your exact wording, remove it.
- "stored assign decision creates a quest, deadline 7 days after the decision" — `run_remediation`. ✔
- Expiry: "expired by the sweep once its deadline has passed" (`deadline_at < as_of`), `expired_at` = deadline. ✔ A completion processed after the deadline also expires a quest (same `expired_at`); the AT wording mentions only the sweep, which is not wrong.
- "policy and sweep invoked, not scheduled" — nothing in `src` calls `run_remediation` or `run_quest_expiry` except tests. ✔
- "no notification hook" — grep of `src/notifications` shows no import of `src.interventions` (it has an unrelated `REMEDIATION` enum value in `models.py`, a notification type, not wired to the policy). ✔
- "implemented for synthetic fixtures" — only synthetic data in tests; no real-data validation. ✔
- "remediation completions only" — `quest_definitions.py` accepted kinds `{"remediation"}`. ✔
- M1 "checkpoint_attempted ingested" with the same ID binding, late window and rule selection — the Step 1 tests cover the 72 h window, dedup-key rule version and event-id binding for checkpoint attempts. ✔

## 4. Left alone on purpose (stale or doubtful text I did NOT change)

- README line "M1 means events ... are validated against versioned schemas ... (currently only `lesson_completed` is ingested)" — now **stale**, but it sits outside the items you listed. It should read "lesson_completed and checkpoint_attempted". Say if you want it fixed.
- README paragraph under the diagram does not mention the intervention policy or the sweep (it lists notifications, abuse detection etc.). Not in your list.
- README top changelog (2026-10-12) not updated; the changelog line was requested for the matrix only.
- `docs/problem-boundaries.md` and other docs were not searched for M4 statements beyond `grep "lesson_completed only"`; other stale statements may exist.
- The matrix M1 row's "Acceptance tests" column still lists only AT-1.

## 5. Mermaid validation

`mmdc` is not installed (`which mmdc` empty); `npx --no-install mmdc` failed (not available offline/locally), and I did not install packages or download a browser. **The diagram was NOT rendered or parsed by a tool.**
I reviewed the new lines by eye against the existing syntax: same `-. text .->` dotted-edge form as the existing `-. retroactive pass, flag-only .->` edge, node labels in quotes with `<br/>`,
no colons or special characters in edge text (I first wrote `invoked:` and removed the colon to stay safe). Treat it as unverified until rendered (GitHub preview or `mmdc`).

## 6. Commands run

```
$ which mmdc            -> (no output)
$ npx --no-install mmdc --version  -> npm error (package not installed)
$ git diff -U0 README.md docs/traceability-matrix.md   (read-only, used to review the changes above)
```
Tests were not re-run (docs only). Last full run: 765 passed, 2 skipped, 1 deselected.
