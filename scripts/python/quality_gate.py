from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_SOURCE = Path("src/flashcards_generator")
RADON_TIMEOUT_SECONDS = 120


def _run_radon(
    arguments: Sequence[str],
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["radon", *arguments],
            check=False,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=RADON_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        print("Radon executable was not found.", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("Radon analysis timed out.", file=sys.stderr)
    except OSError as error:
        print(f"Radon could not be started: {error}", file=sys.stderr)
    return None


def _tool_failed(
    result: subprocess.CompletedProcess[str] | None,
    command_name: str,
) -> bool:
    if result is None:
        print(f"❌ {command_name} failed to run.", file=sys.stderr)
        return True
    if result.returncode == 0:
        return False

    print(
        f"❌ {command_name} failed with exit code {result.returncode}.",
        file=sys.stderr,
    )
    diagnostic = f"{result.stdout}{result.stderr}".strip()
    if diagnostic:
        print(diagnostic, file=sys.stderr)
    return True


def main() -> int:
    print("🔍 Checking complexity (A only)...")
    complexity = _run_radon(("cc", str(PRODUCTION_SOURCE), "--min", "B", "-s"))
    if _tool_failed(complexity, "radon cc"):
        return 1
    if complexity is None:
        return 1
    if complexity.stdout.strip():
        print("❌ QUALITY GATE FAILED")
        print("Complexity threshold: A only")
        print("Functions above rank A:")
        print(complexity.stdout.strip())
        return 1
    print("✅ All production functions are rank A")

    maintainability = _run_radon((
        "mi",
        str(PRODUCTION_SOURCE),
        "--min",
        "B",
        "-s",
    ))
    if _tool_failed(maintainability, "radon mi"):
        return 1
    if maintainability is None:
        return 1
    if maintainability.stdout.strip():
        print("❌ QUALITY GATE FAILED")
        print("Maintainability threshold: A only")
        print("Modules below rank A:")
        print(maintainability.stdout.strip())
        return 1

    print("✅ All production modules are MI rank A")
    return 0


if __name__ == "__main__":
    sys.exit(main())
