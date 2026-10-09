"""Launch the separately installed NiRuLabs server; no source is vendored.

Upstream tools passes api_token positionally to connect(socket_path). Adapt only
that mismatch, retaining the original tools, schemas, results and dispatch. This
file deliberately uses stdlib only before loading the isolated MCP1 runtime.
"""
from __future__ import annotations

import os
from pathlib import Path


def adapted_client(base):
    class TokenAwareClient(base):
        def connect(self, api_token=None):
            # Set authentication before upstream's initial Ping. Do not treat a
            # token as a filesystem path or auto-connect during discovery.
            self._kicad_token = api_token
            socket_path = os.environ.get("UNIFIED_PCB_SOCKET") or None
            # NNG dial/send may wait indefinitely for a nonexistent explicit
            # Unix socket. Fail before opening it; Windows named pipes are not
            # filesystem entries and must retain upstream behavior.
            if socket_path and os.name != "nt" and not Path(socket_path).exists():
                return False
            return super().connect(socket_path=socket_path)
    return TokenAwareClient


def main():
    from kicad_mcp import server, ipc_client
    # Upstream sets recv_timeout but leaves send_timeout infinite. A stale or
    # unreachable native peer must not wedge this backend indefinitely. This
    # affects transport deadlines only; it does not retry any command.
    original_req = ipc_client.pynng.Req0
    def bounded_req(*args, **kwargs):
        kwargs.setdefault("send_timeout", 30000)
        return original_req(*args, **kwargs)
    ipc_client.pynng.Req0 = bounded_req
    server.KiCadIPCClient = adapted_client(server.KiCadIPCClient)
    server.main()


if __name__ == "__main__":
    main()
