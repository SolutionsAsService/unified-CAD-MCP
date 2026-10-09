# Source provenance and composition decision

Inspected 2026-10-09. Canonical integration repository: SolutionsAsService/unified-CAD-MCP. Starting main revision c56ad99d9c2d8f8840b3a79e204b03ab3e6a2403 (README-only). No upstream history rewritten.

Integration authorship and maintenance: [SolutionsAsService](https://github.com/SolutionsAsService). This credit covers the composition/package/configuration/tests/documentation added here, not original upstream implementation or copyrights.

| Backend | Exact selected main revision | Declared version/license |
|---|---|---|
| [SolutionsAsService/kicad-mcp](https://github.com/SolutionsAsService/kicad-mcp) | 82f427027ac77e8090bc64816e6afe42581983eb | 0.18.1; MIT, copyright 2025–2026 blwfish |
| [SolutionsAsService/design-mcp-server](https://github.com/SolutionsAsService/design-mcp-server) | d1cd0f61d6ab631a69ec3de1a7769e94174c6a01 | 0.1.0; no LICENSE file or project license field found |

Source URLs/revisions are machine-readable in sources.lock.json. Each backend is installed into a separate venv from its exact Git URL. This project **does not vendor** backend code, strip attribution, or assign a new license to either source. Public source visibility alone is not a license grant. Obtain/record the design owner's distribution license before embedding/redistributing its code. Authorized local installation is separate from redistribution as part of this repo.

FastMCP's maintained composition/proxy implementation already supplies tool/resource/prompt namespaces and protocol content handling; a custom MCP dialect is unnecessary. Separate processes are essential: inspected KiCad uses FastMCP 4 / MCP 2 while design pins MCP below 2. The backend handshake is MCP 2025-11-25, supported by both. Front clients negotiate through FastMCP normally.

Neither backend source, tests nor docs were edited. New repository files implement packaging, composition, configuration, tests and integration documentation. No upstream AGENTS/global instructions are installed into the host.

locks/*-py312.txt snapshots the tested dependency versions. These are not wheel-hash locks, platform-independent binary guarantees, or an offline distribution. Review source/dependency changes explicitly; no startup updater fetches new backend Git revisions.

## Comprehensive integration extension (2026-10-09)

Starting revision: 42af239c94b6146e3359e565c16619d33ff29cd0.
Re-fetched all three repositories and verified their actual HEAD revisions.
NiRuLabs/kicad-mcp-server is pinned to
2fd14d32fb2532071649cf6779b5e97b524e0a1b (MIT, Copyright (c) 2024).
Its source is installed externally, not vendored; existing two pins remain unchanged.
Reviewed its pyproject build configuration, server imports and proto setup before
installation. No plugin install script, proto compiler, GUI download or repository
example that mutates a board was executed. The package is built by Hatchling;
existing generated protobuf files require protobuf >=6.31.1 despite a weaker
upstream requirement, so the tested isolated lock fixes protobuf 6.33.6.

The integration's launch adapter and coverage extractor are original glue, not
copies of backend source. Coverage metadata includes wire schemas and source
locations/hashes; it does not embed the unlicensed design implementation.
