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
    parser.add_argument("--backend", choices=["kicad", "cad", "pcb", "freecad", "all"], default="all")
    parser.add_argument("--output", type=Path, default=ROOT / "unified-cad.local.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--enable-pcb-schematic", action="store_true", help="Expose 16 experimental IPC schematic tools; compatible native implementation still required")
    parser.add_argument("--enable-freecad-experimental", action="store_true", help="Expose 14 uncommissioned FreeCAD helpers")
    parser.add_argument("--enable-freecad", action="store_true", help="Enable the freecad backend (unrestricted execute_python into a live GUI) in the generated config; implied by --backend freecad")
    parser.add_argument("--enable-drone-tools", action="store_true", help="Enable in-process heuristic drone_* estimators in the generated config")
    args = parser.parse_args()
    asset = args.asset_root.resolve(strict=True)
    project = args.project_root.resolve(strict=True)
    if not asset.is_dir() or not project.is_dir():
        parser.error("Asset and project roots must be existing directories")
    if args.output.exists():
        parser.error("Output configuration exists; preserve it and choose a new --output")
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    config = {"schema_version": 1, "project_root": str(project), "drone_tools": args.enable_drone_tools, "backends": {}}
    for name, source in lock["backends"].items():
        if args.backend not in {name, "all"}:
            continue
        target = ROOT / ".backends" / name
        if target.is_symlink():
            parser.error(f"Refusing symlink environment: {target}")
        if target.exists() and not (target / "pyvenv.cfg").is_file():
            parser.error(f"Refusing non-venv existing directory: {target}")
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if name == "freecad":
            # Stdlib-only upstream: never run its installer or touch GUI config.
            source_dir = target / "source"
            commands = [
                ["git", "init", str(source_dir)],
                ["git", "-C", str(source_dir), "remote", "add", "origin", source["repository"]],
                ["git", "-C", str(source_dir), "fetch", "--depth", "1", "origin", source["commit"]],
                ["git", "-C", str(source_dir), "checkout", "--detach", source["commit"]],
            ]
            print(json.dumps({"backend": name, "install": commands}))
            if not args.dry_run:
                if not target.exists():
                    venv.EnvBuilder(with_pip=False).create(target)
                if source_dir.exists():
                    actual = subprocess.check_output(["git", "-C", str(source_dir), "rev-parse", "HEAD"], text=True).strip()
                    dirty = subprocess.check_output(["git", "-C", str(source_dir), "status", "--porcelain"], text=True)
                    if actual != source["commit"] or dirty:
                        parser.error("Refusing to alter existing FreeCAD source checkout")
                else:
                    for command in commands:
                        subprocess.run(command, check=True, timeout=180)
            # Installed by default, but the backend that exposes unrestricted
            # execute_python stays disabled unless explicitly requested.
            config["backends"][name] = {
                "enabled": args.enable_freecad or args.backend == "freecad",
                "command": str(python), "args": [str(ROOT / "src/unified_cad_mcp/freecad_adapter.py")],
                "cwd": str(project), "env": {"UNIFIED_FREECAD_SOURCE": str(source_dir),
                    "UNIFIED_FREECAD_EXPERIMENTAL": "1" if args.enable_freecad_experimental else "0"},
                "timeout_seconds": 120,
            }
            continue
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
            "command": str(python), "args": ([str(ROOT / "src/unified_cad_mcp/pcb_adapter.py")] if name == "pcb" else ["-m", source["module"]]),
            "cwd": str(project), "env": {"DESIGN_MCP_ASSET_ROOT": str(asset)} if name == "cad" else ({"KICAD_MCP_ENABLE_SCHEMATIC": "1" if args.enable_pcb_schematic else "0"} if name == "pcb" else {"KICAD_MCP_NO_TELEMETRY": "1"}),
            "timeout_seconds": 120,
        }
    if not args.dry_run:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write(chr(10))
        print(f"Configuration written to {args.output}. No MCP host configuration changed.")


if __name__ == "__main__":
    main()
