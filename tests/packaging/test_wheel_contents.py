import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED = {
    "src/schemas/envelope.schema.json",
    "src/schemas/events/lesson_completed.schema.json",
    "src/state_machine/rulesets.json",
}


def test_wheel_contains_schemas_and_rulesets(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "-w", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    assert REQUIRED <= names, f"missing from wheel: {sorted(REQUIRED - names)}"
