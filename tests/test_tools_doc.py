import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from list_tools import DOC, render  # noqa: E402


async def test_tool_list_doc_is_current():
    assert DOC.read_text(encoding="utf-8") == await render(), "run: python scripts/list_tools.py"
