"""Install explicitly selected pinned sources in separate, local Python environments."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--backend", choices=["kicad", "cad", "all"], default="all")
    parser.add_argument("--output", type=Path, default=ROOT / "unified-cad.local.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asset = args.asset_root.resolve(strict=True)
    project = args.project_root.resolve(strict=True)
    if not asset.is_dir() or not project.is_dir():
        parser.error("Asset and project roots must be existing directories")
    if args.output.exists():
        parser.error("Output configuration exists; preserve it and choose a new --output")
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    config = {"schema_version": 1, "project_root": str(project), "backends": {}}
    for name, source in lock["backends"].items():
        if args.backend not in {name, "all"}:
            continue
        target = ROOT / ".backends" / name
        if target.is_symlink():
            parser.error(f"Refusing symlink environment: {target}")
        if target.exists() and not (target / "pyvenv.cfg").is_file():
            parser.error(f"Refusing non-venv existing directory: {target}")
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        requirement = f"git+{source['repository']}.git@{source['commit']}"
        command = [sys.executable, "-m", "pip", "--python", str(python), "install"]
        constraints = ROOT / "locks" / f"{name}-py312.txt"
        if constraints.exists():
            command += ["--constraint", str(constraints)]
        command.append(requirement)
        print(json.dumps({"backend": name, "install": command}))
        if not args.dry_run:
            if not target.exists():
                venv.EnvBuilder(with_pip=False).create(target)
            subprocess.run(command, check=True, timeout=600)
        config["backends"][name] = {
            "command": str(python), "args": ["-m", source["module"]],
            "cwd": str(project), "env": {"DESIGN_MCP_ASSET_ROOT": str(asset)} if name == "cad" else {},
            "timeout_seconds": 120,
        }
    if not args.dry_run:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        print(f"Configuration written to {args.output}. No MCP host configuration changed.")


if __name__ == "__main__":
    main()
