"""回归：deck 更新接口的 outline 字段必须同时接受数组与对象两种形态（零 DB，纯 Pydantic 校验）。

根因（2026-07-06 福仔本机打包环境）：outline 是自由 JSON 列，canonical 形态是数组 OutlineItem[]
（设计 01 契约 / webui / daemon 本地镜像 normalize_outline 归一后的形状）；daemon→云端 sync 的
push_deck_update 推的就是这个数组。而 route 里 UpdateDeckRequest.outline 曾被卡成 `dict | None`，
daemon 推数组时被 Pydantic 拒为 422「输入应为有效的字典」→ deck 永久同步失败、本地行长期 dirty。

修复把 app / agent 两个 route 的 outline 放宽为 `list | dict | None`（对象 {items:[...]} 是云端 MCP
工具 outline.set 的历史写路径，须继续兼容）。本测试锁死这一契约，防止被改回只收 dict。
"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from backend.app.hasn_deck.api.v1.agent.deck import UpdateDeckRequest as AgentUpdateDeckRequest
from backend.app.hasn_deck.api.v1.app.deck import UpdateDeckRequest as AppUpdateDeckRequest

# daemon push_deck_update 实际发送的 canonical 数组形态（normalize_outline 归一后）
_ARRAY_OUTLINE = [
    {'title': '封面', 'key_points': ['主题引入'], 'layout_intent': 'hero'},
    {'title': '目录', 'key_points': ['三个部分']},
]
# 云端 MCP 工具 outline.set 的历史对象形态
_DICT_OUTLINE = {'items': [{'title': '封面'}, {'title': '目录'}]}

_MODELS = (AppUpdateDeckRequest, AgentUpdateDeckRequest)


@pytest.mark.parametrize('model', _MODELS)
def test_outline_accepts_canonical_array(model) -> None:
    """daemon 推送的数组形态必须通过（此前 422 的根因）。"""
    assert model(outline=_ARRAY_OUTLINE).outline == _ARRAY_OUTLINE


@pytest.mark.parametrize('model', _MODELS)
def test_outline_accepts_legacy_object(model) -> None:
    """云端 MCP 工具历史对象形态 {items:[...]} 必须继续兼容。"""
    assert model(outline=_DICT_OUTLINE).outline == _DICT_OUTLINE


@pytest.mark.parametrize('model', _MODELS)
def test_outline_accepts_null(model) -> None:
    """不带 outline / 显式 null 都合法（部分更新）。"""
    assert model().outline is None
    assert model(outline=None).outline is None


@pytest.mark.parametrize('model', _MODELS)
def test_outline_rejects_scalar(model) -> None:
    """仍拒绝标量：放宽到 list|dict 而非任意类型，不接受字符串/数字。"""
    with pytest.raises(ValidationError):
        model(outline='oops')
