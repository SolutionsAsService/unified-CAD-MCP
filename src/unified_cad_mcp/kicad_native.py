"""Startup detection of the native KiCad runtime (flaw F15).

The kicad_* and pcb_* backends start (and list tools) without KiCad installed, so tool
discovery used to imply capabilities that fail later. This probe runs once at startup,
never installs anything and never runs sudo; it reports what was found plus an
actionable install hint for the operator.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
from typing import Any

INSTALL_HINT = (
    "KiCad is not installed for this user. Operator action (requires sudo, not done by this server): "
    "Ubuntu/Debian: 'sudo add-apt-repository --yes ppa:kicad/kicad-9.0-releases && sudo apt update && "
    "sudo apt install --install-recommends kicad'; other OS: https://www.kicad.org/download/ . "
    "Then check 'which kicad-cli' and 'python3 -c \"import pcbnew\"' and restart unified-cad-mcp. "
    "Set UNIFIED_KICAD_CLI to a kicad-cli path if it is not on PATH."
)


def _run(command: list[str], timeout: float) -> tuple[int | None, str]:
    # Own process group: on timeout kill helpers too. subprocess.run(timeout=) kills only the child and then
    # waits for EOF on pipes a surviving grandchild (dbus/X helpers of a GUI build) may hold open forever.
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                text=True, start_new_session=True)
    except OSError as error:
        return None, type(error).__name__
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return None, "TimeoutExpired"
    return proc.returncode, (out or err).strip()[:200]


def detect_kicad(path: str | None = None, python: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """Return {status, kicad_cli, kicad_cli_version, pcbnew, ...}; status is available/partial/missing."""
    cli = os.environ.get("UNIFIED_KICAD_CLI") or shutil.which("kicad-cli", path=path)
    report: dict[str, Any] = {"kicad_cli": cli, "kicad_cli_version": None, "pcbnew_python": None,
                              "pcbnew_version": None}
    if cli:
        code, output = _run([cli, "version"], timeout)
        report["kicad_cli_version"] = output if code == 0 else None
        if code != 0:
            report["kicad_cli_error"] = output or f"exit {code}"
    interpreter = python or shutil.which("python3", path=path)
    if interpreter:
        code, output = _run([interpreter, "-c", "import pcbnew; print(pcbnew.GetBuildVersion())"], timeout)
        if code == 0:
            report["pcbnew_python"], report["pcbnew_version"] = interpreter, output
    if report["kicad_cli_version"] and report["pcbnew_version"]:
        report["status"] = "available"
    elif report["kicad_cli_version"] or report["pcbnew_version"]:
        report["status"] = "partial"
        report["action"] = ("kicad-cli and the pcbnew Python module must both be present for the kicad_* export/DRC "
                            "and pcb_* tools. " + INSTALL_HINT)
    else:
        report["status"] = "missing"
        report["action"] = INSTALL_HINT
    report["scope"] = ("startup probe of the native runtime; kicad_* file tools that only parse text may work "
                       "without it, but DRC/export/netlist/thumbnail and pcb_* IPC need KiCad.")
    return report
