"""Structured results for proxied tools (flaw F10) and explicit STL units/shells (flaw F11).

The pinned upstream CAD server returns its JSON dictionaries only as text content
(structured_content None, no outputSchema). This middleware, applied at the unified
server, declares a permissive object output schema for those tools and fills
structured_content from the text so MCP clients receive structured JSON. The original
content blocks are kept unchanged for compatibility. Tools that already declare an
output schema (or already return structured content) are passed through untouched.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult

from .stl_report import UNIT_TO_MM, scaled_metrics, stl_report

GENERIC_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "description": ("unified-CAD-MCP: upstream JSON result as an object. Non-object JSON is wrapped as "
                    "{\"result\": value}; non-JSON text as {\"text\": ...}. Text content is preserved."),
}
STL_TOOL = "inspect_stl_geometry"
UNITS_PARAMETER = {
    "type": ["string", "null"], "enum": [*sorted(UNIT_TO_MM), None], "default": None,
    "description": ("Length unit of the STL coordinates (STL itself has none). Omitted: read "
                    "<file>.stl.meta.json written by our exporters, else ASSUME mm and say so in result.units."),
}


def structured_from_content(content: list[Any]) -> dict[str, Any]:
    texts = [block.text for block in content if getattr(block, "type", None) == "text"]
    if len(texts) == 1:
        try:
            value = json.loads(texts[0])
        except ValueError:
            return {"text": texts[0]}
        return value if isinstance(value, dict) else {"result": value}
    if not texts:
        return {"content_types": [getattr(block, "type", "unknown") for block in content]}
    return {"text": texts}


class StructuredProxyResults(Middleware):
    def __init__(self, domains: tuple[str, ...] = ("cad",), cad_asset_root: str | None = None):
        self.prefixes = tuple(f"{domain}_" for domain in domains)
        self.cad_asset_root = Path(cad_asset_root).resolve() if cad_asset_root else None

    def _covered(self, name: str) -> bool:
        return name.startswith(self.prefixes)

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        out = []
        for tool in tools:
            if self._covered(tool.name):
                update: dict[str, Any] = {}
                if tool.output_schema is None:
                    update["output_schema"] = GENERIC_OUTPUT_SCHEMA
                if tool.name == f"cad_{STL_TOOL}" and "units" not in tool.parameters.get("properties", {}):
                    parameters = json.loads(json.dumps(tool.parameters))
                    parameters.setdefault("properties", {})["units"] = UNITS_PARAMETER
                    update["parameters"] = parameters
                if update:
                    tool = tool.model_copy(update=update)
            out.append(tool)
        return out

    async def on_call_tool(self, context, call_next):
        name = context.message.name
        if not self._covered(name):
            return await call_next(context)
        units = None
        if name == f"cad_{STL_TOOL}":
            arguments = dict(context.message.arguments or {})
            units = arguments.pop("units", None)
            if units is not None and units not in UNIT_TO_MM:
                return ToolResult(content=f"units must be one of {sorted(UNIT_TO_MM)}",
                                  structured_content={"error": "invalid units", "allowed": sorted(UNIT_TO_MM)},
                                  is_error=True)
            context = context.copy(message=context.message.model_copy(update={"arguments": arguments}))
        result = await call_next(context)
        if result.structured_content is None:
            result = ToolResult(content=result.content, structured_content=structured_from_content(result.content),
                                meta=result.meta, is_error=result.is_error)
        if name == f"cad_{STL_TOOL}" and not result.is_error:
            structured = dict(result.structured_content)
            structured.update(self._stl_extras(structured, context.message.arguments or {}, units))
            text = json.dumps(structured, sort_keys=True)
            from mcp.types import TextContent
            content = [TextContent(type="text", text=text)] + [b for b in result.content if getattr(b, "type", None) != "text"]
            result = ToolResult(content=content, structured_content=structured, meta=result.meta)
        return result

    def _stl_extras(self, upstream: dict[str, Any], arguments: dict[str, Any], units: str | None) -> dict[str, Any]:
        path = None
        relative = upstream.get("path") or arguments.get("relative_path")
        if self.cad_asset_root is not None and isinstance(relative, str):
            candidate = (self.cad_asset_root / relative).resolve()
            if candidate.is_relative_to(self.cad_asset_root) and candidate.is_file():
                path = candidate
        if path is None:
            from .stl_report import resolve_units
            resolution = resolve_units(None, units)
            resolution["warnings"].append("asset root not known to the unified server: sidecar and shell analysis unavailable")
            extras: dict[str, Any] = {"units": resolution, "shell_analysis": {"status": "unavailable"}}
        else:
            extras = stl_report(path, units)
        to_mm = extras["units"]["to_mm"]
        extras["coordinate_units_resolved"] = extras["units"]["units"]
        extras["metrics_mm"] = scaled_metrics(upstream, to_mm)
        extras["units_note"] = ("Upstream coordinate_units is kept verbatim. coordinate_units_resolved / metrics_mm "
                                "use result.units (source and whether it is an assumption are stated there).")
        return extras
