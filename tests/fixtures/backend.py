"""Synthetic protocol peer; never substitute these tests for native CAD verification."""
import asyncio
import json
import os
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent

mcp = FastMCP("synthetic CAD peer")
DOMAIN = os.environ.get("FIXTURE_DOMAIN", "fixture")
if os.environ.get("FIXTURE_PID_FILE"):
    Path(os.environ["FIXTURE_PID_FILE"]).write_text(str(os.getpid()))


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False},
          meta={"fixture": "preserve-me"})
def identify(value: int = 0) -> dict:
    """Return the actual process and working directory to prove routing/session reuse."""
    return {"domain": DOMAIN, "value": value, "pid": os.getpid(), "cwd": os.getcwd(),
            "configured_env": os.environ.get("FIXTURE_CONFIGURED_VALUE"),
            "ambient_secret_present": "UNIFIED_TEST_AMBIENT_SECRET" in os.environ}


@mcp.tool(output_schema={"type": "object", "properties": {"domain": {"type": "string"}},
                         "required": ["domain"]})
def rich_result(fail: bool = False) -> ToolResult:
    return ToolResult(content=[TextContent(type="text", text="backend text"),
                               ImageContent(type="image", data="aGVsbG8=", mime_type="image/png")],
                      structured_content={"domain": DOMAIN},
                      meta={"fixture_result": "unchanged"}, is_error=fail)


@mcp.tool()
async def wait(seconds: float) -> dict:
    # Persist before waiting so mutation replay is observable.
    with Path(f"{DOMAIN}.wait-calls").open("a") as handle:
        handle.write("started\n")
    await asyncio.sleep(seconds)
    return {"waited": seconds}


@mcp.resource("fixture://status")
def status() -> str:
    return json.dumps({"domain": DOMAIN})


@mcp.resource("fixture://items/{item}")
def item(item: str) -> str:
    return f"{DOMAIN}:{item}"


@mcp.prompt()
def explain(subject: str) -> str:
    return f"{DOMAIN} explanation for {subject}"


if __name__ == "__main__":
    mcp.run(show_banner=False)
