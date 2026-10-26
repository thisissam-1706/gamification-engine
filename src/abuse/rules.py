"""Versioned, sha-256-pinned thresholds for rule-based abuse detection.

Same pattern as ``src/state_machine/rules.py``: every number lives in
``abuse_rules.json``; changed content needs a new version, not an edit.
These are conservative placeholders validated only on synthetic fixtures.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any

ABUSE_RULES_FILE = Path(__file__).with_name("abuse_rules.json")
COMPLETION_KINDS = ("lesson", "review", "remediation")
# The one place the default abuse rule version is named.
DEFAULT_ABUSE_RULES_VERSION = "v2"
# Sections added in v2; absent (None) on rule sets that predate them.
OPTIONAL_SECTIONS = ("review_misuse", "receive_burst", "future_dated")


class AbuseRuleConfigError(ValueError):
    """An abuse rule set is missing a field, has an invalid value, or fails its digest."""


def _keys(obj: Any, name: str, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(obj, dict):
        raise AbuseRuleConfigError(f"{name} must be a JSON object")
    missing = keys - obj.keys()
    if missing:
        raise AbuseRuleConfigError(f"{name} is missing {sorted(missing)}")
    unknown = obj.keys() - keys
    if unknown:
        raise AbuseRuleConfigError(f"{name} has unknown keys {sorted(unknown)}")
    return obj


def _pos_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise AbuseRuleConfigError(f"{name} must be a positive int, got {value!r}")
    return value


def _fraction(value: Any, name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 < value < 1
    ):
        raise AbuseRuleConfigError(f"{name} must be a number in (0, 1), got {value!r}")
    return value


def _flag(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise AbuseRuleConfigError(f"{name} must be a boolean, got {value!r}")
    return value


@dataclass(frozen=True)
class ReplayRules:
    window_minutes: int
    min_distinct_replayed_events: int
    flag_conflicting_event_id_reuse: bool


@dataclass(frozen=True)
class DuplicateExploitRules:
    window_minutes: int
    min_distinct_resubmitted_completions: int
    flag_reward_field_in_source_event: bool


@dataclass(frozen=True)
class BurstRules:
    max_completions: int
    window_minutes: int


@dataclass(frozen=True)
class ImpossibleTimingRules:
    default_min_gap_seconds: int
    min_gap_seconds: Mapping[str, int]
    short_gaps_required: int
    short_gap_window_minutes: int
    burst: BurstRules

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_gap_seconds", MappingProxyType(dict(self.min_gap_seconds)))

    def min_gap_for(self, kind: str | None) -> int:
        """Minimum plausible gap before a completion of this kind (absent kind = lesson)."""
        return self.min_gap_seconds.get(kind or "lesson", self.default_min_gap_seconds)


@dataclass(frozen=True)
class RepeatRules:
    window_hours: int
    min_repeat_completions: int


@dataclass(frozen=True)
class MetronomeRules:
    min_events: int
    max_cv: float
    min_mean_gap_seconds: int


@dataclass(frozen=True)
class ScriptedFarmingRules:
    repeat: RepeatRules
    metronome: MetronomeRules


@dataclass(frozen=True)
class ReviewMisuseRules:
    max_reviews_per_lesson: int
    window_hours: int
    orphan_min_lessons: int


@dataclass(frozen=True)
class ReceiveBurstRules:
    max_completions: int
    window_minutes: int


@dataclass(frozen=True)
class FutureDatedRules:
    tolerance_minutes: int
    min_completions: int


@dataclass(frozen=True)
class AbuseRules:
    version: str
    replay: ReplayRules
    duplicate_exploit: DuplicateExploitRules
    impossible_timing: ImpossibleTimingRules
    scripted_farming: ScriptedFarmingRules
    cohort_gaming_threshold: float
    review_misuse: ReviewMisuseRules | None = None
    receive_burst: ReceiveBurstRules | None = None
    future_dated: FutureDatedRules | None = None

    def content(self) -> dict[str, Any]:
        """The pinned content (everything but the version label and the digest)."""
        timing = self.impossible_timing
        content = {
            "replay": {
                "window_minutes": self.replay.window_minutes,
                "min_distinct_replayed_events": self.replay.min_distinct_replayed_events,
                "flag_conflicting_event_id_reuse": self.replay.flag_conflicting_event_id_reuse,
            },
            "duplicate_exploit": {
                "window_minutes": self.duplicate_exploit.window_minutes,
                "min_distinct_resubmitted_completions": self.duplicate_exploit.min_distinct_resubmitted_completions,
                "flag_reward_field_in_source_event": self.duplicate_exploit.flag_reward_field_in_source_event,
            },
            "impossible_timing": {
                "default_min_gap_seconds": timing.default_min_gap_seconds,
                "min_gap_seconds": dict(timing.min_gap_seconds),
                "short_gaps_required": timing.short_gaps_required,
                "short_gap_window_minutes": timing.short_gap_window_minutes,
                "burst": {
                    "max_completions": timing.burst.max_completions,
                    "window_minutes": timing.burst.window_minutes,
                },
            },
            "scripted_farming": {
                "repeat": {
                    "window_hours": self.scripted_farming.repeat.window_hours,
                    "min_repeat_completions": self.scripted_farming.repeat.min_repeat_completions,
                },
                "metronome": {
                    "min_events": self.scripted_farming.metronome.min_events,
                    "max_cv": self.scripted_farming.metronome.max_cv,
                    "min_mean_gap_seconds": self.scripted_farming.metronome.min_mean_gap_seconds,
                },
            },
            "cohort_gaming_threshold": self.cohort_gaming_threshold,
        }
        # Only present when set, so a rule set without them keeps its original digest.
        for name in OPTIONAL_SECTIONS:
            section = getattr(self, name)
            if section is not None:
                content[name] = dict(vars(section))
        return content

    @property
    def digest(self) -> str:
        return content_digest(self.content())


def content_digest(content: Mapping[str, Any]) -> str:
    """SHA-256 of the compact, key-sorted JSON of the rule content (without ``sha256``)."""
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse(version: str, entry: Any) -> AbuseRules:
    name = f"abuse rule set {version}"
    required = {
        "replay",
        "duplicate_exploit",
        "impossible_timing",
        "scripted_farming",
        "cohort_gaming_threshold",
        "sha256",
    }
    present = entry.keys() & set(OPTIONAL_SECTIONS) if isinstance(entry, dict) else set()
    entry = _keys(entry, name, required | present)
    optional: dict[str, Any] = {}
    for section, cls in (
        ("review_misuse", ReviewMisuseRules),
        ("receive_burst", ReceiveBurstRules),
        ("future_dated", FutureDatedRules),
    ):
        if section in present:
            fields = tuple(cls.__dataclass_fields__)
            body = _keys(entry[section], f"{name}.{section}", set(fields))
            optional[section] = cls(*(_pos_int(body[f], f"{section}.{f}") for f in fields))
    replay = _keys(
        entry["replay"],
        f"{name}.replay",
        {"window_minutes", "min_distinct_replayed_events", "flag_conflicting_event_id_reuse"},
    )
    dup = _keys(
        entry["duplicate_exploit"],
        f"{name}.duplicate_exploit",
        {
            "window_minutes",
            "min_distinct_resubmitted_completions",
            "flag_reward_field_in_source_event",
        },
    )
    timing = _keys(
        entry["impossible_timing"],
        f"{name}.impossible_timing",
        {
            "default_min_gap_seconds",
            "min_gap_seconds",
            "short_gaps_required",
            "short_gap_window_minutes",
            "burst",
        },
    )
    gaps = _keys(timing["min_gap_seconds"], f"{name}.min_gap_seconds", set(COMPLETION_KINDS))
    burst = _keys(
        timing["burst"], f"{name}.burst", {"max_completions", "window_minutes"}
    )
    farming = _keys(entry["scripted_farming"], f"{name}.scripted_farming", {"repeat", "metronome"})
    repeat = _keys(
        farming["repeat"], f"{name}.repeat", {"window_hours", "min_repeat_completions"}
    )
    metronome = _keys(
        farming["metronome"],
        f"{name}.metronome",
        {"min_events", "max_cv", "min_mean_gap_seconds"},
    )
    return AbuseRules(
        version=version,
        replay=ReplayRules(
            _pos_int(replay["window_minutes"], "replay.window_minutes"),
            _pos_int(replay["min_distinct_replayed_events"], "replay.min_distinct_replayed_events"),
            _flag(replay["flag_conflicting_event_id_reuse"], "replay.flag_conflicting_event_id_reuse"),
        ),
        duplicate_exploit=DuplicateExploitRules(
            _pos_int(dup["window_minutes"], "duplicate_exploit.window_minutes"),
            _pos_int(
                dup["min_distinct_resubmitted_completions"],
                "duplicate_exploit.min_distinct_resubmitted_completions",
            ),
            _flag(
                dup["flag_reward_field_in_source_event"],
                "duplicate_exploit.flag_reward_field_in_source_event",
            ),
        ),
        impossible_timing=ImpossibleTimingRules(
            _pos_int(timing["default_min_gap_seconds"], "default_min_gap_seconds"),
            {kind: _pos_int(gaps[kind], f"min_gap_seconds.{kind}") for kind in COMPLETION_KINDS},
            _pos_int(timing["short_gaps_required"], "short_gaps_required"),
            _pos_int(timing["short_gap_window_minutes"], "short_gap_window_minutes"),
            BurstRules(
                _pos_int(burst["max_completions"], "burst.max_completions"),
                _pos_int(burst["window_minutes"], "burst.window_minutes"),
            ),
        ),
        scripted_farming=ScriptedFarmingRules(
            RepeatRules(
                _pos_int(repeat["window_hours"], "repeat.window_hours"),
                _pos_int(repeat["min_repeat_completions"], "repeat.min_repeat_completions"),
            ),
            MetronomeRules(
                _pos_int(metronome["min_events"], "metronome.min_events"),
                _fraction(metronome["max_cv"], "metronome.max_cv"),
                _pos_int(metronome["min_mean_gap_seconds"], "metronome.min_mean_gap_seconds"),
            ),
        ),
        cohort_gaming_threshold=_fraction(
            entry["cohort_gaming_threshold"], "cohort_gaming_threshold"
        ),
        **optional,
    )


def load_abuse_rules(path: str | Path | None = None) -> dict[str, AbuseRules]:
    """Loads and verifies the versioned abuse rules; any defect raises AbuseRuleConfigError."""
    source = Path(path) if path is not None else ABUSE_RULES_FILE
    try:
        if path is None:
            text = (files("src.abuse") / "abuse_rules.json").read_text(encoding="utf-8")
        else:
            text = source.read_text(encoding="utf-8")
        document = json.loads(text)
    except (OSError, ValueError) as exc:
        raise AbuseRuleConfigError(f"cannot read abuse rules from {source}: {exc}") from exc
    if not isinstance(document, dict) or not document:
        raise AbuseRuleConfigError("abuse rules file must be a non-empty JSON object")
    rules: dict[str, AbuseRules] = {}
    for version, entry in document.items():
        parsed = _parse(version, entry)
        if entry["sha256"] != parsed.digest:
            raise AbuseRuleConfigError(
                f"abuse rule set {version} content does not match its sha256; "
                "changed rule content needs a new version, not an edit"
            )
        rules[version] = parsed
    return rules


ABUSE_RULES: Mapping[str, AbuseRules] = MappingProxyType(load_abuse_rules())


def get_abuse_rules(version: str | AbuseRules = DEFAULT_ABUSE_RULES_VERSION) -> AbuseRules:
    """Returns a registered abuse rule set, or raises a descriptive error."""
    if isinstance(version, AbuseRules):
        return version
    try:
        return ABUSE_RULES[version]
    except KeyError as exc:
        raise KeyError(f"Unknown abuse rule version: {version}") from exc
