import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from unified_cad_mcp.config import BackendConfig, Configuration, load_config


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), 3601])
def test_timeout_bounds(value):
    with pytest.raises(ValidationError):
        BackendConfig(command="python", timeout_seconds=value)


def test_reject_unknown_backend_namespace(tmp_path):
    with pytest.raises(ValidationError):
        Configuration(project_root=str(tmp_path), backends={"unified": {"command": "python"}})


def test_relative_paths_are_config_relative_not_process_relative(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"project_root": "assets", "backends": {"cad": {"command": "python"}}}))
    config = load_config(path)
    assert config.project_root == str(root)
    assert config.backends["cad"].cwd == str(root)


@pytest.mark.parametrize("change", [{"bogus": 1}, {"enabled": "false"}, {"command": " "}])
def test_bad_backend_settings_fail_closed(change):
    with pytest.raises(ValidationError):
        BackendConfig.model_validate({"command": "python", **change})


def test_missing_project_root_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"project_root": "absent", "backends": {}}))
    with pytest.raises(FileNotFoundError):
        load_config(path)


def test_explicit_cwd_resolves_relative_to_config(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"project_root": "project", "backends": {
        "cad": {"command": "python", "cwd": "work"}}}))
    config = load_config(path)
    assert config.project_root == str(project)
    assert config.backends["cad"].cwd == str(work)
