# Week 4 Fix 8a: Rule version as a trusted ingress setting

Status: **done. Not committed.**

- Week 3 review row 26 (fifth part): the live ingestion route always applied
  rule `v1`. The version now comes from `IngestConfig`, a trusted setting that
  is never read from the envelope. It drives four things:
  - the XP decision;
  - the `dedup_key` expected for new events;
  - the key expected for redeliveries (they keep the version they were
    accepted under);
  - the `rule_version` column of the provenance row.
- `tests/ingestion/test_rule_selection.py` adds 8 tests, and **all 8 pass**.
- Suite excluding `tests/leaderboard` and `tests/notifications`: **210 passed,
  0 failed, 4 skipped**. After Fix 7b it was 202 passed, 4 skipped; the
  difference is the 8 new tests.
- No existing test or assertion changed, and every existing call to
  `ingest_lesson_completed` works unchanged. `src/state_machine/` is
  unchanged.
- This builds on the uncommitted Fix 6a–7b working tree.

## Problem

`ingest_lesson_completed` called `learner.complete_lesson(...)` without a
`rule_version`, so the aggregate always used its default `"v1"`. Fix 6b's
`dedup_key` check and Fix 7a's provenance row were both hard-wired to
`DEFAULT_INGEST_RULE_VERSION`. There was no way to run ingestion under
another registered rule set.

## Change

### 1. `src/ingestion/config.py` (new)

- `IngestConfig(rule_version: str)` is a frozen dataclass. `__post_init__`
  raises `ValueError("Unknown rule version: …")` unless the version is a key
  of `src.state_machine.rules.RULESETS`.
- The check runs when the config is **constructed**. A version registered
  later is accepted only by configs built after it was registered.
- `DEFAULT_CONFIG = IngestConfig(DEFAULT_INGEST_RULE_VERSION)`, which is
  `IngestConfig("v1")`.

### 2. `ingest_lesson_completed(..., config: IngestConfig = DEFAULT_CONFIG)`

It is a keyword-only argument with a default, so every existing call keeps
working and keeps the old behaviour.

### 3. Applied version (`src/ingestion/service.py`)

- **XP decision.** `rule_version=config.rule_version` is passed in both
  `learner.complete_lesson(...)` calls:
  - the first attempt (`service.py:253`);
  - the retry after `IntegrityError` (`service.py:305`).

  The aggregate is not changed: `complete_lesson` already took
  `rule_version` and uses it for `xp_amount_for` and `get_ruleset`.
- **Provenance row.** The accept-path `bind_delivery` stores
  `rule_version=config.rule_version` (`service.py:346`).
- `DEFAULT_INGEST_RULE_VERSION` is no longer referenced in `service.py`. It
  is used only to build `DEFAULT_CONFIG`.

### 4. `dedup_key` check moved

The check used to be the last line of the validation `try` block. It now runs
right after it, at `service.py:182`:

```python
binding = evidence.delivery_binding(event_id)
key_version = (
    binding.rule_version
    if binding is not None and binding.rule_version is not None
    else config.rule_version
)
if envelope["dedup_key"] != expected_dedup_key(event_id, key_version):
    # rejected evidence, reason "dedup_key_mismatch"; no state change
```

- **Precedence.** All earlier rejection reasons are still raised inside the
  `try` block, so they are still checked first: `schema_invalid`,
  `unsupported_schema_version`, `untrusted_producer`,
  `reward_field_in_source_event`. As before, `dedup_key_mismatch` is checked
  before the Fix 6a `duplicate_event_id` / `conflicting_event_id_reuse`
  resolution.
  - `"dedup_key_mismatch"` was removed from the set of recognised
    `ValidationError` messages, because nothing raises it as a
    `ValidationError` any more.
- **Evidence.** A mismatch writes the same `rejected` row as before: reason,
  `action_id` and `event_id`, with no `original_ref`.
- **Why the bound version wins.** If a delivery was accepted under `v1` and
  the config later becomes `v2`, a redelivery of that same message (key
  `:v1`) is recognised as `duplicate_event_id`. It is not rejected as a
  mismatch. A brand-new `event_id` has no binding, so it must use the current
  config's version.

## Tests (`tests/ingestion/test_rule_selection.py`)

The `rule_v2` fixture registers a second rule set with
`monkeypatch.setitem(RULESETS, "v2", RuleSet(xp_amount=150, quest_targets=dict(RULESETS["v1"].quest_targets), badge_threshold=7, badge_id="week_warrior_v1"))`
and returns `IngestConfig("v2")`. `monkeypatch` removes `v2` after each test.

| Test | What it proves |
|---|---|
| `test_ingest_config_rejects_an_unknown_rule_version` | `IngestConfig("v9")` raises `ValueError` with `Unknown rule version: v9`. `IngestConfig("v1")` works, and `DEFAULT_CONFIG == IngestConfig("v1")`. |
| `test_ingest_config_is_frozen` | Assigning `rule_version` raises `AttributeError`. |
| `test_default_behaviour_is_unchanged` | With no `config` argument, the key is `"…08a1:v1"`, the delivery is accepted with `xp_total` 100, the event store has `XPAwarded ("xp:cmp-A", 100, "v1")`, and the provenance `rule_version` is `"v1"`. |
| `test_configured_rule_set_is_applied_and_bound_into_new_keys` | With `config=v2`, a new event keyed `"…08a1:v2"` is accepted with `xp_total` 150, `XPAwarded ("xp:cmp-A", 150, "v2")` and provenance `"v2"`. A new event keyed `:v1` is rejected as `dedup_key_mismatch`: `xp_total` stays 150, there are no new events, exactly one rejected row, and no binding. |
| `test_old_message_redelivered_after_config_change_is_a_duplicate` | Accept under the default config, close, and reopen with `config=v2`. The identical old message (`:v1`) gives `duplicate_event_id`. The same `event_id` re-keyed `:v2` gives `dedup_key_mismatch`. `xp_total` stays 100, the only award is `("xp:cmp-A", 100, "v1")`, and the row still says `"v1"`. |
| `test_row_without_rule_version_falls_back_to_the_config` | A row whose `rule_version` is `NULL` (pre-Fix-7a) falls back to the config: under `v2` the old `:v1` message is a `dedup_key_mismatch`, and under the default it is `duplicate_event_id`. This pins the specified fallback. |
| `test_earlier_rejection_reasons_keep_precedence_under_a_config` | With `config=v2` and a wrong-version key, an untrusted producer still gives `untrusted_producer`, and a reward field in the source still gives `reward_field_in_source_event`. |
| `test_retry_after_integrity_error_uses_the_configured_version` | `application.save` raises `IntegrityError` once, which forces the reload-and-retry path. The delivery is accepted after 2 `save` calls with `XPAwarded ("xp:cmp-A", 150, "v2")` and provenance `"v2"`. If the retry ignored the config, the award would be 100 / `"v1"`. |

## Before / after

| Case | Before | After |
|---|---|---|
| Default call, key `:v1` | accepted, 100 XP, `XPAwarded.rule_version "v1"`, row `"v1"` | the same |
| `config=v2` (not possible before), new event, key `:v2` | (no `config` argument; `:v2` was a `dedup_key_mismatch`) | accepted, **150 XP**, `XPAwarded.rule_version "v2"`, row `"v2"` |
| `config=v2`, new event, key `:v1` | (accepted, 100 XP under the hard-wired `v1`) | rejected, `dedup_key_mismatch`, no state change |
| Message accepted under `v1`, redelivered under `config=v2` | (n/a) | `duplicate_event_id` |
| suite (excluding leaderboard and notifications) | 202 passed, 4 skipped | 210 passed, 4 skipped |

## Known limits

- **Pre-7a rows.** A row with `NULL` `rule_version` uses the current config.
  After a config change, such a redelivery is rejected as
  `dedup_key_mismatch` instead of being recognised as a duplicate. It is
  still rejected and still changes no state. This was the specified
  behaviour.
- **Crash-gap redeliveries.** If an event was accepted under `v1`, crashed
  before its binding was written, and is then redelivered after a change to
  `v2`, the key check runs before the crash-gap backfill and sees no binding.
  The redelivery is rejected as `dedup_key_mismatch`; no XP is double counted.
- **Wiring.** Nothing selects `config` yet. Callers must pass it, which a
  later step will wire up. `RULESETS` holds only `v1`, so in production
  `DEFAULT_CONFIG` is still the only valid setting.
