from pathlib import Path
import sys

import pytest

from unified_cad_mcp.config import BackendConfig, Configuration

FIXTURE = Path(__file__).parent / "fixtures" / "backend.py"


@pytest.fixture
def config(tmp_path):
    return Configuration(project_root=str(tmp_path), backends={
        name: BackendConfig(command=sys.executable, args=[str(FIXTURE)], cwd=str(tmp_path),
                            env={"FIXTURE_DOMAIN": name, "FIXTURE_PID_FILE": str(tmp_path / f"{name}.pid")},
                            timeout_seconds=10)
        for name in ["kicad", "cad"]
    })
