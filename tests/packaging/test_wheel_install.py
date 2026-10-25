import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SCRIPT = textwrap.dedent(
    """
    import sys
    from datetime import datetime, timezone
    from uuid import uuid4

    from src.ingestion.evidence import EvidenceStore
    from src.ingestion.service import ingest_lesson_completed
    from src.state_machine.learner import Learner, LearnerApplication

    db = sys.argv[1]
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db}
    )
    evidence = EvidenceStore(db + ".evidence")
    try:
        learner = Learner()
        application.save(learner)
        event_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        envelope = {
            "event_id": event_id,
            "event_type": "learning.lesson_completed",
            "schema_version": 1,
            "occurred_at": now,
            "ingested_at": now,
            "subject_id": str(learner.id),
            "producer": "learning-svc",
            "correlation_id": str(uuid4()),
            "causation_id": None,
            "dedup_key": event_id + ":v1",
            "payload": {
                "completion_id": "completion-1",
                "lesson_id": "lesson-1",
                "timezone": "Asia/Kolkata",
            },
            "provenance": {"source": "client"},
        }
        result = ingest_lesson_completed(
            application, evidence, envelope, authenticated_producer="learning-svc"
        )
        print(result.status)
    finally:
        evidence.close()
        application.close()
    """
)


@pytest.mark.integration
def test_installed_wheel_ingests_outside_checkout(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "-w", str(wheel_dir)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    wheel = next(wheel_dir.glob("*.whl"))

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, capture_output=True)
    venv_python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", str(wheel)], check=True, capture_output=True
    )

    work_dir = tmp_path / "elsewhere"
    work_dir.mkdir()
    script = work_dir / "ingest_one.py"
    script.write_text(SCRIPT, encoding="utf-8")
    completed = subprocess.run(
        [str(venv_python), str(script), str(work_dir / "events.db")],
        cwd=work_dir,
        capture_output=True,
        text=True,
        env={"PATH": "", "HOME": str(tmp_path)},
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "accepted"
