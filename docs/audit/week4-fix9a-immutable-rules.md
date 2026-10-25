# Week 4 Fix 9a: Immutable rule sets from a verified artifact

Status: **implemented. One existing test is blocked on a decision. Not
committed.**

- Week 3 review row 26 (seventh part):
  - a frozen `RuleSet` still held a mutable `quest_targets` dict;
  - `RULESETS` could be changed at runtime, with no versioned configuration
    artifact.
- Source changes are limited to `src/state_machine/rules.py` and the new
  `src/state_machine/rulesets.json`.
- `tests/state_machine/test_rules_immutable.py` adds 28 tests, and **all 28
  pass**.
- Suite excluding `tests/leaderboard` and `tests/notifications`: **239 passed,
  1 failed, 4 skipped**. After Fix 8b it was 212 passed, 4 skipped.
  - The 1 failure is
    `tests/state_machine/test_rule_versioning.py::test_historical_reward_replay_uses_persisted_amount_after_rule_update`.
    See "Pending decision". It was **not** edited, because the needed change
    is outside "patch `_REGISTRY` instead of `RULESETS`".
- This builds on the uncommitted Fix 6a–8b working tree.

## Problem

`RuleSet` was `@dataclass(frozen=True)`, but `quest_targets: dict[str, int]`
was the caller's own dict. So:

- `get_ruleset("v1").quest_targets["lesson_starter_v1"] = 99` silently
  changed v1 for the whole process;
- mutating the dict that was passed to the constructor did the same.

`RULESETS`, `XP_AMOUNTS` and `QUEST_DEFINITIONS` were plain module dicts, and
v1's content lived only in source code. Nothing tied a version name to its
content, so "v1" could mean different things in different deployments.

## Change (`src/state_machine/rules.py`)

| Item | Where | What |
|---|---|---|
| Artifact | `src/state_machine/rulesets.json` (new) | v1, exactly as before (`xp_amount 100`, `quest_targets {"lesson_starter_v1": 3}`, `badge_threshold 7`, `badge_id "week_warrior_v1"`), plus `"sha256"`. |
| `RULESETS_FILE` | `rules.py:13` | `Path(__file__).with_name("rulesets.json")` |
| `RuleConfigError(ValueError)` | `rules.py:17` | New exception for any missing key, invalid value or digest mismatch. |
| Validation | `_validate_content` | `xp_amount` and `badge_threshold` must be positive `int`s (`bool` is rejected). `badge_id` must be a non-blank `str`. `quest_targets` must be a mapping of non-empty `str` → positive `int`. |
| `RuleSet.__post_init__` | `rules.py:69` | Validates the four fields, then sets `quest_targets` to `MappingProxyType(dict(quest_targets))` through `object.__setattr__`: a copy behind a read-only view. The annotation is now `Mapping[str, int]`. |
| `RuleSet.digest` | `rules.py:79` | SHA-256 of `json.dumps({4 content fields}, sort_keys=True, separators=(",", ":"))`. |
| `load_rulesets(path=None)` | `rules.py:86` | Reads the JSON. It requires exactly the 4 content keys plus `sha256` per version; unknown keys are rejected too, to catch typos. It builds a `RuleSet` (which validates) and recomputes the digest. A mismatch raises `RuleConfigError` with "changed rule content needs a new version, not an edit". Unreadable, empty or malformed files raise `RuleConfigError`. |
| Registry | `rules.py:119-120` | `_REGISTRY = load_rulesets()` and `RULESETS = MappingProxyType(_REGISTRY)`. This is a **live** read-only view, so a test that patches `_REGISTRY` is seen through `RULESETS`. A comment marks `_REGISTRY` as a test-only seam. |
| Compatibility views | `rules.py:123-` | `XP_AMOUNTS` and `QUEST_DEFINITIONS` (including its inner dict) are now `MappingProxyType`s with the same content as before. |

`get_ruleset`, `quest_target_for`, `xp_amount_for`, `badge_qualifies`,
`calculate_streak` and `local_activity_date` keep their signatures and
bodies. `src/ingestion/config.py` (`rule_version in RULESETS`) works unchanged
on the proxy.

### v1 golden digest

```
v1 compact JSON : {"badge_id":"week_warrior_v1","badge_threshold":7,"quest_targets":{"lesson_starter_v1":3},"xp_amount":100}
v1 sha256       : ba6d0a3821ebdb1358840ec7a0dd2f67136b4eeb3062d79f3369b53cda6b4430
```

This literal is pinned in `test_v1_digest_is_golden`. **Changing v1's
content requires a new version** (`v2`, …) with its own digest. Never edit v1
and update the literal: stored `XPAwarded` facts say `rule_version "v1"`, and
that must keep meaning this exact content.

## Existing test files touched (registry patch target only)

| File | Change |
|---|---|
| `tests/ingestion/test_rule_selection.py` | `rule_v2` fixture: `monkeypatch.setitem(RULESETS, …)` → `monkeypatch.setitem(_REGISTRY, …)`. The import gains `_REGISTRY`. 1 patch. |
| `tests/ingestion/test_rule_version_change.py` | `_register_v2`, plus the step-2 "v1 worth 999" patch and its restore: 3 `monkeypatch.setitem(RULESETS, …)` → `_REGISTRY`. The import gains `_REGISTRY`. 3 patches. |
| `tests/state_machine/test_rule_versioning.py` | 4 tests register `v2` directly. In each, `RULESETS.get("v2")`, `RULESETS["v2"] = RuleSet(…)`, `del RULESETS["v2"]` and `RULESETS["v2"] = original` now use `_REGISTRY` (16 lines). The import changes `RULESETS` to `_REGISTRY`. |

No assertion changed in any of them. Reads such as `RULESETS["v1"]` and
`"v2" not in RULESETS` were left as they are; they read through the proxy.

## Pending decision: `test_historical_reward_replay_uses_persisted_amount_after_rule_update`

**Resolved in Fix 9b** ([week4-fix9b-rule-digest.md](week4-fix9b-rule-digest.md)): the test was rewritten to replace v1 in `_REGISTRY` with a 999-XP rule set before a restart and replay. The text below is the 9a record.

The test does `XP_AMOUNTS["v1"] = 150` to simulate "the global rule config
changes to E = 150", and restores it with `XP_AMOUNTS["v1"] = 100` in its
`finally`. `XP_AMOUNTS` is now read-only, so the first line raises
`TypeError: 'mappingproxy' object does not support item assignment`. This is
not a `RULESETS` patch, so it was left unedited.

Also, **nothing in `src/` reads `XP_AMOUNTS`.** `xp_amount_for` reads
`RULESETS`. So the `= 150` line never changed any rule, and the test's
"config changed" premise was not actually exercised before this fix.

Options:

1. Replace the two `XP_AMOUNTS` lines with
   `monkeypatch.setitem(_REGISTRY, "v1", RuleSet(xp_amount=150, …v1's other fields…))`.
   This makes the premise real, and its assertions (replay gives 100, the
   award is 100 / `v1`) then actually prove AT-5. It needs a `monkeypatch`
   parameter.
2. Delete the two `XP_AMOUNTS` lines. Its assertions are unchanged, but it
   tests replay with no config change.

No assertion changes in either option.

## Tests (`tests/state_machine/test_rules_immutable.py`, 28)

| Test | What it proves |
|---|---|
| `test_quest_targets_cannot_be_mutated` | Item assignment and `del` on `get_ruleset("v1").quest_targets` raise `TypeError`; the value is still 3. |
| `test_constructor_dict_is_copied` | Mutating and extending the dict passed to `RuleSet(...)` leaves the RuleSet at `{"lesson_starter_v1": 3}`. |
| `test_registry_and_compatibility_views_are_read_only` | `RULESETS["x"] = …`, `del RULESETS["v1"]`, `XP_AMOUNTS["v1"] = 150` and `QUEST_DEFINITIONS[…]["target"] = 99` all raise `TypeError`. The values are unchanged. |
| `test_ruleset_fields_stay_frozen` | `xp_amount` assignment raises `AttributeError`. |
| `test_packaged_file_loads_exact_v1` | `RULESETS_FILE` is `src/state_machine/rulesets.json`. Loading it gives only `v1` = 100 / `{"lesson_starter_v1": 3}` / 7 / `"week_warrior_v1"`, equal to `RULESETS`. |
| `test_v1_digest_is_golden` | `get_ruleset("v1").digest` and the file's `sha256` both equal `ba6d0a38…4430`. |
| `test_get_ruleset_behaviour_is_unchanged` | The default is v1, a `RuleSet` passes through, and an unknown version gives `KeyError "Unknown rule version: v9"`. |
| `test_tampered_value_with_old_digest_is_rejected` | `xp_amount` 150 with the old sha256 raises `RuleConfigError` ("does not match its sha256"). |
| `test_tampered_quest_target_with_old_digest_is_rejected` | The same for a quest target changed from 3 to 2. |
| `test_missing_key_is_rejected` (×5) | Each of the 4 content keys and `sha256` → `RuleConfigError` ("missing"). |
| `test_invalid_value_is_rejected_by_loader_and_constructor` (×12) | 0, −100, `"100"` and `True` for `xp_amount`; 0 and `7.0` for `badge_threshold`; `""` and `"   "` for `badge_id`; target 0, target −3, an empty quest id, and a list for `quest_targets`. Each raises `RuleConfigError` from both `load_rulesets` and `RuleSet(...)`. |
| `test_unknown_key_and_malformed_files_are_rejected` | A misspelt key, an empty object, a JSON list, broken JSON and a missing file → `RuleConfigError`. |
| `test_a_correctly_digested_new_version_loads` | A file with v1 and a correctly digested v2 (150 XP) loads, and the digests differ. |

## Before / after

| | Before | After |
|---|---|---|
| `get_ruleset("v1").quest_targets["lesson_starter_v1"] = 99` | succeeds; v1 changed process-wide | `TypeError` |
| mutating the dict passed to `RuleSet(...)` | changes the RuleSet | no effect (copied) |
| `RULESETS["x"] = …` / `XP_AMOUNTS["v1"] = 150` | succeeds | `TypeError` |
| source of v1 content | Python literal in `rules.py` | `rulesets.json`, verified by sha256 `ba6d0a38…4430` at import |
| `RuleSet(xp_amount=0, …)` | accepted | `RuleConfigError` |
| suite (excluding leaderboard and notifications) | 212 passed, 4 skipped | 239 passed, **1 failed** (pending decision), 4 skipped |

## Packaging note

`src/state_machine/rulesets.json` is **package data**, and `rules.py` loads it
at import time. `pyproject.toml` currently uses
`[tool.setuptools.packages.find]` with no `package-data` or
`include-package-data` setting, so a built wheel or sdist would **not** include
the JSON. The installed package would then fail at import with
`RuleConfigError: cannot read rule sets …`. The later packaging step must add
it, for example
`[tool.setuptools.package-data] "src.state_machine" = ["rulesets.json"]`, and
should test it by importing from an installed wheel. Running from the source
tree, as the tests do, is unaffected.

## Known limits

- **Shallow immutability.** `MappingProxyType` blocks writes through the view,
  but `rules._REGISTRY` is still a plain dict that any code can import and
  mutate. The comment marks it as a test-only seam; nothing enforces that.
- **The digest checks content, not origin.** It proves a version's content has
  not drifted from the digest written next to it. Anyone who edits both the
  content and the `sha256` produces a "valid" file. The golden test in CI
  catches that for v1.
