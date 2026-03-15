import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest_plugins = [
    "tests.fixtures.domain_fixtures",
    "tests.fixtures.infrastructure_fixtures",
]
