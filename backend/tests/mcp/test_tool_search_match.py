"""ToolDirectoryService._match_tools 模糊搜索匹配契约。

重点：多词自然语言 query 不再因「整串子串」匹配不到而返回空（旧实现的 LOW bug），
而是按命中词数打分返回；单词 query 行为与旧实现保持一致（命中集 + 注册顺序不变）。
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from backend.app.mcp.tool_directory import ToolDirectoryService


@dataclass(frozen=True)
class _StubTool:
    name: str
    description: str
    source: str = 'platform'


def _match(tools: list[_StubTool], query: str, source: str = 'all') -> list[_StubTool]:
    service = ToolDirectoryService(MagicMock())
    return service._match_tools(tools, query, source)  # type: ignore[arg-type]


def test_multi_word_query_no_longer_returns_empty() -> None:
    quote = _StubTool('hasn.finance.quote', '获取股票的实时市场行情 market quote')
    deck = _StubTool('hasn.deck.create', '新建一份演示文稿')
    # 旧实现把 "finance market quote" 当整串子串匹配 → 任一 haystack 都不含该连续串 → 返回空。
    matched = _match([quote, deck], 'finance market quote')
    assert quote in matched
    assert deck not in matched


def test_more_matched_terms_rank_higher() -> None:
    three = _StubTool('hasn.finance.quote', '行情 finance market quote 全命中')
    one = _StubTool('hasn.market.board', '只命中 market 一个词')
    # 注册顺序故意把命中少的放前面，验证按命中词数（相关度）重排而非保持原序。
    matched = _match([one, three], 'finance market quote')
    assert matched == [three, one]


def test_single_word_query_preserves_membership_and_order() -> None:
    first = _StubTool('hasn.finance.quote', '行情查询')
    second = _StubTool('hasn.finance.history', '历史行情')
    other = _StubTool('hasn.deck.create', '演示文稿')
    # 单词 query：所有命中者同分 → 稳定排序保持注册顺序；非命中者排除。
    matched = _match([first, second, other], 'finance')
    assert matched == [first, second]


def test_empty_query_returns_all_in_order() -> None:
    a = _StubTool('hasn.a.one', 'alpha')
    b = _StubTool('hasn.b.two', 'beta')
    # 空 query 沿用旧语义：'' 子串命中一切 → 全量返回、保持注册顺序。
    assert _match([a, b], '') == [a, b]


def test_source_filter_applies_before_scoring() -> None:
    platform_tool = _StubTool('hasn.finance.quote', '行情 finance', source='platform')
    local_tool = _StubTool('hasn.deck.export', '导出 finance', source='local')
    matched = _match([platform_tool, local_tool], 'finance', source='local')
    assert matched == [local_tool]
