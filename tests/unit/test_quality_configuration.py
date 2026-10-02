import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FAKE_RADON = (
    "#!/usr/bin/env python3\n"
    "import json\n"
    "import os\n"
    "import sys\n"
    "stage = sys.argv[1]\n"
    "with open(os.environ['QUALITY_GATE_RADON_CALLS_FILE'], 'a', "
    "encoding='utf-8') as calls_file:\n"
    "    calls_file.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    "code, stdout, stderr = json.loads("
    "os.environ['QUALITY_GATE_RADON_RESULTS'])[stage]\n"
    "sys.stdout.write(stdout)\n"
    "sys.stderr.write(stderr)\n"
    "raise SystemExit(code)\n"
)


def _run_quality_gate(
    tmp_path: Path,
    radon_results: dict[str, tuple[int, str, str]],
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    task_executable = shutil.which("task")
    assert task_executable is not None

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls_file = tmp_path / "radon-calls.jsonl"
    fake_radon = fake_bin / "radon"
    fake_radon.write_text(_FAKE_RADON, encoding="utf-8")
    fake_radon.chmod(0o700)

    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join((str(fake_bin), environment["PATH"]))
    environment["QUALITY_GATE_RADON_RESULTS"] = json.dumps(radon_results)
    environment["QUALITY_GATE_RADON_CALLS_FILE"] = str(calls_file)
    result = subprocess.run(
        [task_executable, "quality-gate"],
        check=False,
        cwd=PROJECT_ROOT,
        capture_output=True,
        env=environment,
        text=True,
        timeout=30,
    )
    calls = [
        json.loads(line)
        for line in calls_file.read_text(encoding="utf-8").splitlines()
    ]
    return result, calls


def test_ci_quality_gate_uses_the_enforced_task() -> None:
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    quality_gate = project["tool"]["taskipy"]["tasks"]["quality-gate"]
    assert quality_gate == "python scripts/python/quality_gate.py"

    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )
    triggers = workflow.split("\nconcurrency:\n", maxsplit=1)[0]
    for event in ("push", "pull_request"):
        match = re.search(
            rf"(?ms)^  {event}:(.*?)(?=^  [a-z_]+:|\Z)",
            triggers,
        )
        assert match is not None
        assert "- 'scripts/python/**'" in match.group(1)

    typecheck = workflow.index("name: Run TY type check")
    typecheck_step = re.search(
        r"(?ms)^      - name: Run TY type check\n(.*?)(?=^      - name: |\Z)",
        workflow,
    )
    assert typecheck_step is not None
    assert (
        "run: uv run --frozen ty check src/flashcards_generator "
        "scripts/python/quality_gate.py"
    ) in typecheck_step.group(1)

    radon_gate = workflow.index("name: Enforce Radon rank A")
    artifact_delivery = workflow.index("Upload coverage to Codecov")
    assert typecheck < radon_gate < artifact_delivery


@pytest.mark.parametrize(
    (
        "radon_results",
        "expected_status",
        "expected_message",
        "expected_stages",
    ),
    [
        (
            {"cc": (0, "", ""), "mi": (0, "", "")},
            0,
            "All production modules are MI rank A",
            ("cc", "mi"),
        ),
        (
            {
                "cc": (0, "src/example.py - generate - C (10)\n", ""),
                "mi": (0, "", ""),
            },
            1,
            "Functions above rank A:",
            ("cc",),
        ),
        (
            {
                "cc": (0, "", ""),
                "mi": (0, "src/example.py - B (49.50)\n", ""),
            },
            1,
            "Modules below rank A:",
            ("cc", "mi"),
        ),
        (
            {"cc": (2, "", ""), "mi": (0, "", "")},
            1,
            "radon cc failed with exit code 2",
            ("cc",),
        ),
        (
            {"cc": (0, "", ""), "mi": (2, "", "")},
            1,
            "radon mi failed with exit code 2",
            ("cc", "mi"),
        ),
    ],
)
def test_quality_gate_enforces_radon_results(
    tmp_path: Path,
    radon_results: dict[str, tuple[int, str, str]],
    expected_status: int,
    expected_message: str,
    expected_stages: tuple[str, ...],
) -> None:
    result, calls = _run_quality_gate(tmp_path, radon_results)

    assert result.returncode == expected_status
    assert expected_message in result.stdout + result.stderr
    assert calls == [
        [stage, "src/flashcards_generator", "--min", "B", "-s"]
        for stage in expected_stages
    ]
