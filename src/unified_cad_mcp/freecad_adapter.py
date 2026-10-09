"""Launch the separately pinned NiRuLink server with bounded, local-only IPC.

No upstream code is vendored. Its schemas and code generation are retained;
provisional helpers are opt-in. This is NOT a Python sandbox or transaction layer.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import struct
import sys
import time

PIN = "b60aad723d4607dd1789ef3b1b287571d237afe8"
# Digests of the only imported upstream files; filled from audited pinned source.
DIGESTS = {'nirulink_mcp_server.py': 'b5e667148cf9c0e52daf4a7b90ae1816f820f4804ce6f8597ad1340efd803315', 'nirulink_bridge.py': '975d844c0492f5aacf303ae6cf54398d8dccd9a58fb561d31b9b5282a6c45513'}
CORE = {"connect_to_freecad", "execute_python"}
MAX_FRAME = 16 * 1024 * 1024


class LocalClient:
    """One persistent connection, explicit reconnect, no mutation replay."""
    def __init__(self, host="localhost", port=9876):
        if host not in {"localhost", "127.0.0.1"}:
            raise ValueError("Only literal localhost/127.0.0.1 is allowed; no remote CAD execution")
        configured = int(os.environ.get("UNIFIED_FREECAD_PORT", "9876"))
        if type(port) is not int or not 1 <= port <= 65535 or port != configured:
            raise ValueError("Port must match operator UNIFIED_FREECAD_PORT (default 9876)")
        self.host, self.port = "127.0.0.1", port
        self.socket = None
        self.timeout = float(os.environ.get("UNIFIED_FREECAD_TIMEOUT", "30"))
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 60:
            raise ValueError("UNIFIED_FREECAD_TIMEOUT must be finite, >0 and <=60 seconds")

    def connect(self, timeout=None):
        self.disconnect()
        try:
            self.socket = socket.create_connection((self.host, self.port), self.timeout)
        except OSError as error:
            raise ConnectionError("FreeCAD listener unavailable; install/start native FreeCAD GUI and pinned NiRuLink addon locally") from error
        return True

    def disconnect(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def is_connected(self):
        return self.socket is not None

    def execute(self, code):
        if self.socket is None:
            raise ConnectionError("Not connected to FreeCAD; explicitly call connect_to_freecad")
        body = json.dumps({"code": code}).encode("utf-8")
        if len(body) > MAX_FRAME:
            raise ValueError("Request exceeds 16 MiB frame limit")
        deadline = time.monotonic() + self.timeout
        def receive(count):
            data = bytearray()
            while len(data) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("FreeCAD response deadline exceeded")
                self.socket.settimeout(remaining)
                chunk = self.socket.recv(count - len(data))
                if not chunk:
                    raise ConnectionError("FreeCAD disconnected")
                data.extend(chunk)
            return bytes(data)
        try:
            self.socket.settimeout(self.timeout)
            self.socket.sendall(struct.pack(">I", len(body)) + body)
            size = struct.unpack(">I", receive(4))[0]
            if not 0 < size <= MAX_FRAME:
                raise ValueError("Invalid/oversized FreeCAD response frame")
            result = json.loads(receive(size))
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError("Invalid FreeCAD response envelope")
        except Exception as error:
            self.disconnect()
            raise ConnectionError("FreeCAD transport failed; outcome UNKNOWN. Inspect effects before explicit reconnect; never replay automatically") from error
        if not result["ok"]:
            # Upstream helpers otherwise report success even when execution fails.
            raise RuntimeError(json.dumps(result, ensure_ascii=False))
        return result


def validate_arguments(tool, args):
    """Validate upstream JSON schemas and prevent helper string interpolation injection.

    execute_python is intentionally unrestricted code with current-user access.
    Paths are evaluated on the FreeCAD host, not against a pretend asset sandbox.
    """
    schema = tool["inputSchema"]
    properties = schema.get("properties", {})
    if not isinstance(args, dict) or set(args) - set(properties):
        raise ValueError("Unknown tool arguments")
    if set(schema.get("required", [])) - set(args):
        raise ValueError("Missing required tool arguments")
    for key, value in args.items():
        rule = properties[key]
        kind = rule["type"]
        valid = {"string": isinstance(value, str), "integer": type(value) is int,
                 "number": type(value) in (int, float) and math.isfinite(value),
                 "boolean": type(value) is bool, "array": isinstance(value, list)}[kind]
        if not valid or ("enum" in rule and value not in rule["enum"]):
            raise ValueError(f"Invalid argument: {key}")
        strings = value if kind == "array" else [value] if kind == "string" else []
        for item in strings:
            if not isinstance(item, str):
                raise ValueError(f"Expected string items: {key}")
            if tool["name"] != "execute_python" and any(ord(c) < 32 or c in chr(34) + chr(92) for c in item):
                raise ValueError("Helper strings cannot contain quotes, backslashes or control newlines; use forward-slash paths")
        if key == "filepath":
            # Support absolute Windows drive paths even when MCP runs on Linux.
            from pathlib import PurePosixPath, PureWindowsPath
            if not (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()):
                raise ValueError("Export filepath must be absolute on the FreeCAD host")


def load_upstream(source):
    source = Path(source).resolve(strict=True)
    for filename, expected in DIGESTS.items():
        if hashlib.sha256((source / "server" / filename).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Pinned NiRuLink source mismatch: {filename}")
    sys.path.insert(0, str(source / "server"))
    spec = importlib.util.spec_from_file_location("nirulink_mcp_server", source / "server/nirulink_mcp_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_adapter(module, experimental=False):
    captured = []
    original_send = module.send_result
    module.send_result = lambda _id, value: captured.append(value)
    module.handle_tools_list(0, {})
    module.send_result = original_send
    tools = {t["name"]: t for t in captured[0]["tools"]}
    enabled = tools if experimental else {n: t for n, t in tools.items() if n in CORE}
    module.NiRuLinkClient = LocalClient
    module.log = lambda _message: None  # upstream logs request code/project contents
    original_call = module.handle_tool_call

    def call(request_id, params):
        name = params.get("name")
        try:
            if name not in enabled:
                raise ValueError("Unknown or disabled provisional FreeCAD tool")
            validate_arguments(tools[name], params.get("arguments", {}))
            if name == "connect_to_freecad" and module.client:
                module.client.disconnect()
                module.client = None
            original_call(request_id, params)
        except Exception as error:
            module.send_result(request_id, {"content": [{"type": "text", "text": str(error)}], "isError": True})
    module.handle_tool_call = call
    module.handle_tools_list = lambda request_id, _params: module.send_result(request_id, {"tools": list(enabled.values())})
    return module


def main():
    source = os.environ.get("UNIFIED_FREECAD_SOURCE")
    if not source:
        raise SystemExit("UNIFIED_FREECAD_SOURCE must point to the separately installed pinned source checkout")
    gate = os.environ.get("UNIFIED_FREECAD_EXPERIMENTAL", "0")
    if gate not in {"0", "1"}:
        raise SystemExit("UNIFIED_FREECAD_EXPERIMENTAL must be 0 or 1")
    module = install_adapter(load_upstream(source), experimental=gate == "1")
    try:
        module.main()
    finally:
        if module.client:
            module.client.disconnect()


if __name__ == "__main__":
    main()
