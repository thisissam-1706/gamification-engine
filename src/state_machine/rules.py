"""Small deterministic reward and streak rules used at the decision boundary."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo

RULESETS_FILE = Path(__file__).with_name("rulesets.json")
# Opt-in rule sets with a decay schedule; kept out of rulesets.json so the
# default registry stays exactly {"v1"} (see docs/audit/week5-m6-abuse-detection.md).
DECAY_RULESETS_FILE = Path(__file__).with_name("rulesets_decay.json")
_CONTENT_FIELDS = ("xp_amount", "quest_targets", "badge_threshold", "badge_id")
_DECAY_KEYS = frozenset({"first_repeat_percent", "step_xp"})


class RuleConfigError(ValueError):
    """A rule set is missing a field, has an invalid value, or fails its digest."""


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_content(
    xp_amount: Any, quest_targets: Any, badge_threshold: Any, badge_id: Any
) -> None:
    if not _is_positive_int(xp_amount):
        raise RuleConfigError(f"xp_amount must be a positive int, got {xp_amount!r}")
    if not _is_positive_int(badge_threshold):
        raise RuleConfigError(
            f"badge_threshold must be a positive int, got {badge_threshold!r}"
        )
    if not isinstance(badge_id, str) or not badge_id.strip():
        raise RuleConfigError(f"badge_id must be a non-empty string, got {badge_id!r}")
    if not isinstance(quest_targets, Mapping):
        raise RuleConfigError(
            f"quest_targets must be a mapping, got {type(quest_targets).__name__}"
        )
    for quest_id, target in quest_targets.items():
        if not isinstance(quest_id, str) or not quest_id:
            raise RuleConfigError(f"quest id must be a non-empty string, got {quest_id!r}")
        if not _is_positive_int(target):
            raise RuleConfigError(
                f"quest target for {quest_id} must be a positive int, got {target!r}"
            )


def _validate_decay(decay: Any) -> None:
    if not isinstance(decay, Mapping):
        raise RuleConfigError(f"decay must be a mapping, got {type(decay).__name__}")
    if set(decay) != _DECAY_KEYS:
        raise RuleConfigError(
            f"decay must have exactly the keys {sorted(_DECAY_KEYS)}, got {sorted(map(str, decay))}"
        )
    percent, step = decay["first_repeat_percent"], decay["step_xp"]
    if not _is_positive_int(percent) or percent > 100:
        raise RuleConfigError(
            f"decay first_repeat_percent must be an int in 1..100, got {percent!r}"
        )
    if not _is_positive_int(step):
        raise RuleConfigError(f"decay step_xp must be a positive int, got {step!r}")


def _content_digest(
    xp_amount: int,
    quest_targets: Mapping[str, int],
    badge_threshold: int,
    badge_id: str,
    decay: Mapping[str, int] | None = None,
) -> str:
    content: dict[str, Any] = {
        "xp_amount": xp_amount,
        "quest_targets": dict(quest_targets),
        "badge_threshold": badge_threshold,
        "badge_id": badge_id,
    }
    # Only present when set, so a rule set without decay keeps its original digest.
    if decay is not None:
        content["decay"] = dict(decay)
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RuleSet:
    xp_amount: int
    quest_targets: Mapping[str, int]
    badge_threshold: int
    badge_id: str
    decay: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        _validate_content(
            self.xp_amount, self.quest_targets, self.badge_threshold, self.badge_id
        )
        if self.decay is not None:
            _validate_decay(self.decay)
            object.__setattr__(self, "decay", MappingProxyType(dict(self.decay)))
        # Copy, then expose read-only: the caller's dict can no longer reach in.
        object.__setattr__(
            self, "quest_targets", MappingProxyType(dict(self.quest_targets))
        )

    @property
    def digest(self) -> str:
        """SHA-256 of the compact, key-sorted JSON of the content fields.

        ``decay`` is part of the content only when present.
        """
        return _content_digest(
            self.xp_amount,
            self.quest_targets,
            self.badge_threshold,
            self.badge_id,
            self.decay,
        )


def _parse_rulesets(text: str, source: object) -> dict[str, RuleSet]:
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise RuleConfigError(f"cannot read rule sets from {source}: {exc}") from exc
    if not isinstance(document, dict) or not document:
        raise RuleConfigError("rule sets file must be a non-empty JSON object")

    required = {*_CONTENT_FIELDS, "sha256"}
    allowed = {*required, "decay"}
    rulesets: dict[str, RuleSet] = {}
    for version, entry in document.items():
        if not isinstance(entry, dict):
            raise RuleConfigError(f"rule set {version} must be a JSON object")
        missing = required - entry.keys()
        if missing:
            raise RuleConfigError(f"rule set {version} is missing {sorted(missing)}")
        unknown = entry.keys() - allowed
        if unknown:
            raise RuleConfigError(f"rule set {version} has unknown keys {sorted(unknown)}")
        ruleset = RuleSet(
            **{field: entry[field] for field in (*_CONTENT_FIELDS, "decay") if field in entry}
        )
        if entry["sha256"] != ruleset.digest:
            raise RuleConfigError(
                f"rule set {version} content does not match its sha256; "
                "changed rule content needs a new version, not an edit"
            )
        rulesets[version] = ruleset
    return rulesets


def load_rulesets(path: str | Path | None = None) -> dict[str, RuleSet]:
    """Loads and verifies the versioned rule sets; any defect raises RuleConfigError."""
    source = Path(path) if path is not None else RULESETS_FILE
    try:
        if path is None:
            text = (files("src.state_machine") / "rulesets.json").read_text(encoding="utf-8")
        else:
            text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleConfigError(f"cannot read rule sets from {source}: {exc}") from exc
    return _parse_rulesets(text, source)


def load_decay_rulesets() -> dict[str, RuleSet]:
    """Loads and verifies the packaged opt-in rule sets that carry a decay schedule."""
    try:
        text = (files("src.state_machine") / "rulesets_decay.json").read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleConfigError(f"cannot read decay rule sets: {exc}") from exc
    return _parse_rulesets(text, DECAY_RULESETS_FILE)


# Test-only seam: tests register extra versions with monkeypatch.setitem on
# _REGISTRY. Production code reads RULESETS, a read-only live view of it.
_REGISTRY: dict[str, RuleSet] = load_rulesets()
RULESETS: Mapping[str, RuleSet] = MappingProxyType(_REGISTRY)

# Compatibility views for callers that inspect the former configuration names.
XP_AMOUNTS: Mapping[str, int] = MappingProxyType({"v1": RULESETS["v1"].xp_amount})
QUEST_DEFINITIONS: Mapping[str, Mapping[str, int]] = MappingProxyType(
    {
        "lesson_starter_v1": MappingProxyType(
            {"target": RULESETS["v1"].quest_targets["lesson_starter_v1"]}
        )
    }
)


def register_decay_rulesets() -> list[str]:
    """Opts in to the packaged decay rule sets (v2); idempotent. Returns their versions.

    Without this call RULESETS is exactly the contents of rulesets.json, so the
    default behaviour of every existing caller is unchanged.
    """
    loaded = load_decay_rulesets()
    for version, ruleset in loaded.items():
        existing = _REGISTRY.get(version)
        if existing is not None and existing.digest != ruleset.digest:
            raise RuleConfigError(f"rule version {version} is already registered with other content")
        _REGISTRY[version] = ruleset
    return sorted(loaded)


def get_ruleset(version: str | RuleSet = "v1") -> RuleSet:
    """Returns a registered rule set, or raises a descriptive error."""
    if isinstance(version, RuleSet):
        return version
    try:
        return RULESETS[version]
    except KeyError as exc:
        raise KeyError(f"Unknown rule version: {version}") from exc


def quest_target_for(
    quest_id: str, rule_version: str | RuleSet = "v1"
) -> int:
    """Returns the required completion target for a given quest ID."""
    ruleset = get_ruleset(rule_version)
    if quest_id not in ruleset.quest_targets:
        raise KeyError(f"Unknown quest: {quest_id}")
    return ruleset.quest_targets[quest_id]


def local_activity_date(occurred_at: str | datetime, timezone_name: str) -> str:
    """Computes the ISO calendar date (YYYY-MM-DD) in the learner's IANA timezone."""
    if isinstance(occurred_at, str):
        clean_ts = occurred_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
    else:
        dt = occurred_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    tz = ZoneInfo(timezone_name)
    return dt.astimezone(tz).date().isoformat()
def xp_amount_for(rule_version: str | RuleSet = "v1") -> int:
    """Returns the currently configured amount for a rule version."""
    return get_ruleset(rule_version).xp_amount


def xp_for_completion(
    rule_version: str | RuleSet = "v1", prior_eligible_repeats: int = 0
) -> int:
    """XP for a completion with N prior eligible completions of the same lesson.

    N == 0 earns the base amount E. Without a decay schedule every completion
    earns E. With one, N >= 1 earns
    ``max(0, E * first_repeat_percent // 100 - step_xp * (N - 1))``.
    """
    if (
        not isinstance(prior_eligible_repeats, int)
        or isinstance(prior_eligible_repeats, bool)
        or prior_eligible_repeats < 0
    ):
        raise ValueError(
            f"prior_eligible_repeats must be a non-negative int, got {prior_eligible_repeats!r}"
        )
    ruleset = get_ruleset(rule_version)
    base = ruleset.xp_amount
    if ruleset.decay is None or prior_eligible_repeats == 0:
        return base
    first = base * ruleset.decay["first_repeat_percent"] // 100
    return max(0, first - ruleset.decay["step_xp"] * (prior_eligible_repeats - 1))


def badge_qualifies(
    badge_id: str, new_streak: int, rule_version: str | RuleSet = "v1"
) -> bool:
    """Returns whether the current state qualifies for a supported badge."""
    ruleset = get_ruleset(rule_version)
    return badge_id == ruleset.badge_id and new_streak >= ruleset.badge_threshold


def calculate_streak(
    activity_dates: Iterable[str],
    freezes: Iterable[tuple[str, str]],
) -> dict[str, tuple[int, int, int]]:
    """Returns ``activity_date -> (streak, remaining, consumed)``.

    Freeze records are sorted by acquisition date and can cover gaps once they
    have been acquired.  The returned values are facts for each date, so
    callers can emit append-only corrections without recalculating during
    replay.
    """
    ordered_dates = sorted({date.fromisoformat(value) for value in activity_dates})
    ordered_freezes = sorted(
        ((freeze_id, date.fromisoformat(acquired)) for freeze_id, acquired in freezes),
        key=lambda item: (item[1], item[0]),
    )
    consumed: set[str] = set()
    results: dict[str, tuple[int, int, int]] = {}
    streak = 0
    previous: date | None = None
    for current in ordered_dates:
        used_for_date = 0
        if previous is None:
            streak = 1
        else:
            missing_days = (current - previous).days - 1
            if missing_days == 0:
                streak += 1
            elif missing_days > 0:
                available = [
                    (freeze_id, acquired)
                    for freeze_id, acquired in ordered_freezes
                    if freeze_id not in consumed
                    and acquired <= current
                ]
                if len(available) >= missing_days:
                    for freeze_id, _ in available[:missing_days]:
                        consumed.add(freeze_id)
                    used_for_date = missing_days
                    streak += 1
                else:
                    streak = 1
        remaining = len(ordered_freezes) - len(consumed)
        results[current.isoformat()] = (streak, remaining, used_for_date)
        previous = current
    return results
