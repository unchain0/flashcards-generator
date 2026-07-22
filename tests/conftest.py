"""Pytest configuration."""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest_plugins = [
    "tests.fixtures.domain_fixtures",
    "tests.fixtures.infrastructure_fixtures",
    "tests.fixtures.adapter_fixtures",
]


@pytest.fixture
def temp_dirs():
    """Create temporary input and output directories."""
    temp_dir = Path(tempfile.mkdtemp())
    input_dir = temp_dir / "input"
    output_dir = temp_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    yield input_dir, output_dir

    # Cleanup
    shutil.rmtree(temp_dir, ignore_errors=True)
