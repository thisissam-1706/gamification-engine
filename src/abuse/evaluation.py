"""Efficacy measurement for the abuse detectors against labeled synthetic learners.

Pure over labels and signals. The numbers describe behaviour on synthetic
fixtures only; they are not evidence about real learners.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from uuid import UUID

from src.abuse.detectors import SIGNAL_TYPES, Signal
from src.ingestion.evidence import EvidenceStore
from src.state_machine.learner import LearnerApplication

BENIGN = "benign"
ATTACKER = "attacker"
BORDERLINE = "borderline"


@dataclass(frozen=True)
class LabeledLearner:
    """Ground truth for one synthetic learner.

    ``truth_types`` are the abuse types the persona really commits. For
    borderline personas ``expected_detected`` documents what the detectors are
    known to output (a miss or a false positive); it is never tuned away.
    """

    learner_id: str
    persona: str
    category: str
    truth_types: frozenset[str]
    expected_detected: frozenset[str] | None = None
    note: str = ""


@dataclass(frozen=True)
class TypeMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    recall: float | None


@dataclass(frozen=True)
class BorderlineResult:
    persona: str
    truth_types: tuple[str, ...]
    detected_types: tuple[str, ...]
    expected_detected: tuple[str, ...]
    behaved_as_labeled: bool
    note: str


@dataclass(frozen=True)
class EvaluationReport:
    per_type: dict[str, TypeMetrics]
    benign_learners: int
    benign_false_positives: int
    attacker_learners: int
    attackers_fully_detected: int
    borderline: list[BorderlineResult]
    signals_total: int
    evidence_backed_rate: float | None


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def detected_types(signals: Iterable[Signal]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for signal in signals:
        found.setdefault(signal.learner_id, set()).add(signal.signal_type)
    return found


def evaluate(
    labels: Iterable[LabeledLearner],
    signals: Iterable[Signal],
    resolves: Callable[[Signal, str], bool] | None = None,
) -> EvaluationReport:
    """Scores signals against labels; borderline learners are reported, not scored."""
    labels = sorted(labels, key=lambda label: (label.persona, label.learner_id))
    signals = list(signals)
    found = detected_types(signals)
    counts = {t: [0, 0, 0] for t in SIGNAL_TYPES}  # tp, fp, fn
    benign = attackers = benign_fp = fully = 0
    borderline: list[BorderlineResult] = []
    for label in labels:
        got = found.get(label.learner_id, set())
        if label.category == BORDERLINE:
            expected = label.expected_detected or frozenset()
            borderline.append(
                BorderlineResult(
                    label.persona,
                    tuple(sorted(label.truth_types)),
                    tuple(sorted(got)),
                    tuple(sorted(expected)),
                    got == set(expected),
                    label.note,
                )
            )
            continue
        for signal_type in SIGNAL_TYPES:
            truth, hit = signal_type in label.truth_types, signal_type in got
            if truth and hit:
                counts[signal_type][0] += 1
            elif hit:
                counts[signal_type][1] += 1
            elif truth:
                counts[signal_type][2] += 1
        if label.category == BENIGN:
            benign += 1
            benign_fp += bool(got)
        else:
            attackers += 1
            fully += label.truth_types <= got
    backed = None
    if resolves is not None and signals:
        ok = sum(all(resolves(signal, ref) for ref in signal.evidence_refs) for signal in signals)
        backed = ok / len(signals)
    return EvaluationReport(
        per_type={
            t: TypeMetrics(tp, fp, fn, _ratio(tp, tp + fp), _ratio(tp, tp + fn))
            for t, (tp, fp, fn) in counts.items()
        },
        benign_learners=benign,
        benign_false_positives=benign_fp,
        attacker_learners=attackers,
        attackers_fully_detected=fully,
        borderline=borderline,
        signals_total=len(signals),
        evidence_backed_rate=backed,
    )


def store_resolver(
    application: LearnerApplication, evidence_store: EvidenceStore
) -> Callable[[Signal, str], bool]:
    """Resolves an evidence ref against the stores themselves, not the detector's facts."""
    rows = {record.id: record for record in evidence_store.list()}

    def resolves(signal: Signal, ref: str) -> bool:
        kind, _, value = ref.partition(":")
        if not value:
            return False
        if kind == "evidence":
            row = rows.get(int(value)) if value.isdigit() else None
            return row is not None and row.learner_id == signal.learner_id
        if kind == "event":
            binding = evidence_store.delivery_binding(value)
            return binding is not None and binding.learner_id == signal.learner_id
        if kind == "completion":
            learner = application.repository.get(UUID(signal.learner_id))
            return value in learner.completed_lesson_ids
        return False

    return resolves
