"""P4-B / Q1 — builtin App 工具说明.md ⟷ DB manifest 一致性。

零漂移：每个 builtin App 源目录下的 `工具说明.md` 列出的 (mcp_name, required_scopes,
risk) 必须与 manifest（运行时权威源）逐条一致。文档列的工具必须真实存在于 manifest。
"""

from __future__ import annotations

from pathlib import Path

from backend.app.hasn.service.ai_native_builtin_manifests import (
    COMMUNITY_AI_NATIVE_MANIFEST,
    KNOWLEDGE_AI_NATIVE_MANIFEST,
)
from backend.app.hasn_creator.manifest import CREATOR_AI_NATIVE_MANIFEST
from backend.app.hasn_deck.manifest import DECK_AI_NATIVE_MANIFEST
from backend.app.hasn_finance.manifest import FINANCE_AI_NATIVE_MANIFEST
from backend.app.hasn_publish.manifest import PUBLISH_AI_NATIVE_MANIFEST
from backend.app.hasn_task.service.ai_native_manifest import HASN_TASK_AI_NATIVE_MANIFEST

_APPS_DIR = Path(__file__).resolve().parents[2] / 'app' / 'mcp' / 'apps'


def _parse_doc_rows(app_id: str) -> dict[str, tuple[set[str], str]]:
    """解析 工具说明.md 的工具表 → {mcp_name: (required_scopes_set, risk)}。"""
    doc = (_APPS_DIR / app_id / '工具说明.md').read_text(encoding='utf-8')
    rows: dict[str, tuple[set[str], str]] = {}
    for line in doc.splitlines():
        line = line.strip()
        if not line.startswith('| `hasn.'):
            continue
        cells = [c.strip().strip('`') for c in line.strip('|').split('|')]
        if len(cells) < 3:
            continue
        mcp_name = cells[0]
        scopes = {s.strip().strip('`') for s in cells[1].split('/') if s.strip()}
        risk = cells[2]
        rows[mcp_name] = (scopes, risk)
    return rows


def _manifest_rows(manifest: dict) -> dict[str, tuple[set[str], str]]:
    rows: dict[str, tuple[set[str], str]] = {}
    for cap in manifest.get('capabilities', []):
        rows[cap['mcp_name']] = (set(cap.get('required_scopes') or []), str(cap.get('risk_level') or 'low'))
    return rows


def test_community_doc_matches_manifest() -> None:
    assert _parse_doc_rows('community') == _manifest_rows(COMMUNITY_AI_NATIVE_MANIFEST)


def test_knowledge_doc_matches_manifest() -> None:
    assert _parse_doc_rows('knowledge') == _manifest_rows(KNOWLEDGE_AI_NATIVE_MANIFEST)


def test_deck_doc_matches_manifest() -> None:
    assert _parse_doc_rows('deck') == _manifest_rows(DECK_AI_NATIVE_MANIFEST)


def test_publish_doc_matches_manifest() -> None:
    assert _parse_doc_rows('publish') == _manifest_rows(PUBLISH_AI_NATIVE_MANIFEST)


def test_hasn_task_doc_matches_manifest() -> None:
    assert _parse_doc_rows('hasn_task') == _manifest_rows(HASN_TASK_AI_NATIVE_MANIFEST)


def test_creator_doc_matches_manifest() -> None:
    assert _parse_doc_rows('creator') == _manifest_rows(CREATOR_AI_NATIVE_MANIFEST)


def test_finance_doc_matches_manifest() -> None:
    assert _parse_doc_rows('finance') == _manifest_rows(FINANCE_AI_NATIVE_MANIFEST)
