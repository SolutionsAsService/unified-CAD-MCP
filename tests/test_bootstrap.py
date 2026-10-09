import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_dry_run_creates_no_config_and_uses_source_pins(tmp_path):
    output = tmp_path / "new-config.json"
    result = subprocess.run([sys.executable, str(ROOT / "scripts/bootstrap_backends.py"),
                             "--project-root", str(tmp_path), "--asset-root", str(tmp_path),
                             "--output", str(output), "--dry-run"], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert not output.exists()
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    for source in lock["backends"].values():
        assert source["commit"] in result.stdout
    assert "--constraint" in result.stdout


def test_existing_configuration_is_not_overwritten(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("preserve me")
    result = subprocess.run([sys.executable, str(ROOT / "scripts/bootstrap_backends.py"),
                             "--project-root", str(tmp_path), "--asset-root", str(tmp_path),
                             "--output", str(output)], capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert output.read_text() == "preserve me"


def test_gitignore_covers_local_secrets_and_state():
    private = [".env", ".env.local", ".env.production", ".backends/cad/bin/python",
               ".venv/bin/python", ".cache/index.json", ".jcodemunch/index.json",
               ".state/session.json", ".design-mcp/state.json", ".kicad_mcp/history.json",
               "secrets/token.json", ".secrets/token", "unified-cad.local.json",
               "unified-cad.operator.local.json"]
    result = subprocess.run(["git", "check-ignore", "--stdin"], input="\n".join(private),
                            capture_output=True, text=True, cwd=ROOT, timeout=10)
    assert result.returncode == 0
    assert set(result.stdout.splitlines()) == set(private)
