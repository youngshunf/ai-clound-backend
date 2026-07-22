"""平台工具 · deck 域 真实 service 测试（禁 mock，TOOLMIG2-P3，福仔选 B：完整迁 deck）。

验证从 hasn-node 本地 hasn-mcp 迁来的 deck 工具（云端分身可完整创作演示文稿）：
- 注册齐全（12 个），工具名/命名空间/execution_location/scope 与 manifest + deck.rs 1:1；
- scope split：读 4（get/list/style.list/style.get）无 scope；写 8 = deck:manage；
- 骨架校验器（page_skeleton）与 daemon Rust 校验器同口径（合格/不合格用例）；
- 真实 PG 往返：create→outline.set→page.write_batch（含 rejected）→get→reorder→edit→delete-page→delete。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_deck_tools.py
无 DB 时跳过（不伪造）。deck 鉴权走 owner 继承（分身 owner==deck owner → manager），故无需 seed 分身行。
"""

from __future__ import annotations

import operator
import uuid

from typing import TYPE_CHECKING

import pytest

from backend.app.hasn_deck.service.page_skeleton import validate_page_skeleton
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.deck import DECK_TOOLS

if TYPE_CHECKING:
    from backend.app.mcp.tools.base import BaseTool

_VALID_HTML = '<section><h1>封面</h1><p>正文一段</p></section>'
# 未闭合 <section> → ⑨ 标签配平不一致 → 不合格。
_BAD_HTML = '<section><h1>坏页</h1>'


def _tool(name: str) -> BaseTool:
    for t in DECK_TOOLS:
        if t.name == name:
            return t
    raise AssertionError(f'deck 工具未注册: {name}')


def _agent_ctx(owner_hasn_id: str | None, agent_hasn_id: str = 'a_deck_tools_test') -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        agent_status='active',
        metadata={},
        agent_name='演示测试分身',
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_deck_tools_test',
    )


async def _db_reachable() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.db import async_db_session

        async with async_db_session() as db:
            await db.execute(text('SELECT 1'))
    except Exception:
        return False
    else:
        return True


# ── 注册/契约（无需 DB）────────────────────────────────────────────────────────
_EXPECTED_NAMES = {
    'hasn.deck.create',
    'hasn.deck.get',
    'hasn.deck.list',
    'hasn.deck.outline.set',
    'hasn.deck.page.write_batch',
    'hasn.deck.page.write',
    'hasn.deck.page.edit',
    'hasn.deck.page.delete',
    'hasn.deck.page.reorder',
    'hasn.deck.finalize',
    'hasn.deck.delete',
    'hasn.deck.style.list',
    'hasn.deck.style.get',
}
_READS = {'hasn.deck.get', 'hasn.deck.list', 'hasn.deck.style.list', 'hasn.deck.style.get'}


def test_deck_tools_register_exactly() -> None:
    """12 个工具全注册（4 读 + 8 写）。"""
    names = {t.name for t in DECK_TOOLS}
    assert names == _EXPECTED_NAMES, f'差异: {names ^ _EXPECTED_NAMES}'


def test_deck_tools_are_cloud_platform() -> None:
    for t in DECK_TOOLS:
        assert t.source == 'platform'
        assert t.namespace == 'hasn.deck'
        assert t.execution_location == 'cloud'


def test_deck_tools_scope_split() -> None:
    """读 4 无 scope；写 8 统一 deck:manage（跨仓与本地 hasn-mcp deck.rs 对齐）。"""
    for t in DECK_TOOLS:
        if t.name in _READS:
            assert t.required_scopes == [], f'{t.name} 读类不应有 scope'
        else:
            assert t.required_scopes == ['deck:manage'], f'{t.name} 写类应声明 deck:manage'


@pytest.mark.asyncio
async def test_deck_tool_rejects_missing_owner_identity() -> None:
    """主人身份缺失时必须拒绝读取或修改主人隔离的演示文稿。"""
    with pytest.raises(RuntimeError, match='缺少 owner_hasn_id'):
        await _tool('hasn.deck.list').execute(_agent_ctx(None), {})


def test_required_fields_match_contract() -> None:
    assert 'required' not in _tool('hasn.deck.create').input_schema
    assert _tool('hasn.deck.get').input_schema['required'] == ['deck_id']
    assert _tool('hasn.deck.outline.set').input_schema['required'] == ['deck_id', 'pages']
    assert _tool('hasn.deck.page.write').input_schema['required'] == ['deck_id', 'position', 'html']
    assert _tool('hasn.deck.page.reorder').input_schema['required'] == ['deck_id', 'page_ids']
    assert _tool('hasn.deck.style.get').input_schema['required'] == ['style_id']


def test_deck_scope_in_aggregated_catalog() -> None:
    from backend.app.mcp.scopes import SCOPE_CATALOG

    assert SCOPE_CATALOG['deck:manage']['domain'] == 'deck'


# ── 骨架校验器（纯函数，无需 DB；与 daemon Rust 校验器同口径）──────────────────────
def test_skeleton_accepts_valid_fragment() -> None:
    assert validate_page_skeleton(_VALID_HTML) is None


def test_skeleton_rejects_empty() -> None:
    assert validate_page_skeleton('   ') == '页 HTML 为空'


def test_skeleton_rejects_full_document() -> None:
    reason = validate_page_skeleton('<html><head></head><body><div>x</div></body></html>')
    assert reason is not None
    assert '<html>' in reason


def test_skeleton_rejects_script_src() -> None:
    reason = validate_page_skeleton('<section><script src="https://cdn/x.js"></script></section>')
    assert reason is not None
    assert '<script src>' in reason


def test_skeleton_rejects_unbalanced_tag() -> None:
    reason = validate_page_skeleton(_BAD_HTML)
    assert reason is not None
    assert '<section>' in reason and '不一致' in reason


def test_skeleton_rejects_new_chart_and_anime() -> None:
    chart = validate_page_skeleton('<section><canvas></canvas><script>const c = new Chart(x);</script></section>')
    assert chart is not None and 'new Chart' in chart
    anime = validate_page_skeleton('<section><script>anime(x);</script></section>')
    assert anime is not None and 'anime(...)' in anime


def test_skeleton_rejects_chart_without_fixed_height() -> None:
    """canvas + PPT.createChart 但无固定高度容器 → 大概率渲染空白，硬拒（与 daemon 同口径）。"""
    bad = validate_page_skeleton(
        '<div class="flex-1 w-full"><canvas id="c"></canvas></div>'
        "<script>PPT.createChart(document.getElementById('c'), {});</script>"
    )
    assert bad is not None and '固定像素高度' in bad

    # 各种固定高度写法均应放行。
    for ok in (
        '<div class="h-[300px]"><canvas id="c"></canvas></div><script>PPT.createChart(\'#c\', {});</script>',
        '<div class="h-64"><canvas id="c"></canvas></div><script>PPT.createChart(\'#c\', {});</script>',
        '<div class="md:h-80"><canvas id="c"></canvas></div><script>PPT.createChart(\'#c\', {});</script>',
        '<div class="aspect-video"><canvas id="c"></canvas></div><script>PPT.createChart(\'#c\', {});</script>',
        '<div style="height:300px"><canvas id="c"></canvas></div><script>PPT.createChart(\'#c\', {});</script>',
        '<div><canvas id="c" height="300"></canvas></div><script>PPT.createChart(\'#c\', {});</script>',
    ):
        assert validate_page_skeleton(ok) is None, f'应放行固定高度图表：{ok}'

    # min-h-[300px] 不撑高 Chart.js → 仍应被拒。
    only_min = validate_page_skeleton(
        '<div class="min-h-[300px]"><canvas id="c"></canvas></div>'
        "<script>PPT.createChart('#c', {});</script>"
    )
    assert only_min is not None and '固定像素高度' in only_min

    # 装饰性 canvas（无 PPT.createChart）不强制高度。
    assert validate_page_skeleton('<div class="flex-1"><canvas id="deco"></canvas></div>') is None


def test_skeleton_rejects_cropping_image_object_fit() -> None:
    """<img object-cover/object-fill> 会裁切/拉伸图片 → 硬拒，必须 object-contain（与 daemon 同口径）。"""
    # object-cover 裁切 → 拒。
    cover = validate_page_skeleton(
        '<div class="w-full h-[400px]"><img src="hasn://asset/x" class="w-full h-full object-cover"></div>'
    )
    assert cover is not None and 'object-contain' in cover

    # object-fill 拉伸 → 拒。
    assert validate_page_skeleton('<img src="hasn://asset/x" class="w-full object-fill">') is not None

    # inline object-fit:cover → 拒。
    assert validate_page_skeleton('<img src="hasn://asset/x" style="object-fit:cover">') is not None

    # object-contain（等比缩放完整显示）应放行。
    assert (
        validate_page_skeleton(
            '<div class="w-full h-[400px]"><img src="hasn://asset/x" class="w-full h-full object-contain"></div>'
        )
        is None
    )

    # 无 object-fit 的普通 <img> 放行。
    assert validate_page_skeleton('<img src="hasn://asset/x" class="w-full">') is None

    # 满铺背景用 CSS background（bg-cover）而非 <img> → 不触发本规则。
    assert (
        validate_page_skeleton(
            '<div class="absolute inset-0 bg-cover" style="background-image:url(\'hasn://asset/x\')"></div>'
        )
        is None
    )


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio(loop_scope='module')
async def test_deck_lifecycle_roundtrip_real_db() -> None:
    """真实 PG：create→outline.set→page.write_batch(含 rejected)→get→reorder→edit→delete-page→delete。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import text

    from backend.database.db import async_db_session

    owner = f'h_deck_tool_{uuid.uuid4().hex[:18]}'
    ctx = _agent_ctx(owner)
    deck_id = None
    try:
        # 1) 建空 deck
        created = await _tool('hasn.deck.create').execute(ctx, {'title': '调研演示', 'language': 'zh'})
        deck_id = created['deck_id']
        assert isinstance(deck_id, str) and deck_id
        assert created['deck']['title'] == '调研演示'
        # 分身用工具建 deck 自动绑定它自己为协作分身（首页纯派发流程依赖：绑定落在 create 这一刻，
        # 不再靠预建带 bound_agent_id 的空 deck）。
        assert created['deck']['bound_agent_id'] == ctx.agent_hasn_id
        # doc36 §3.2：写工具返回体必须带 `uri`——登记那一刻算出的地址就地返给分身，
        # 而不是算完扔掉、只留一个裸 deck_id 让分身不知道怎么打开自己刚建的东西。
        assert created['uri'] == f'hasn://deck/{deck_id}'

        # 2) 写大纲（存储为 {'items': pages}）
        await _tool('hasn.deck.outline.set').execute(
            ctx, {'deck_id': deck_id, 'pages': [{'title': '封面'}, {'title': '要点'}]}
        )

        # 3) 批量写 2 合格页（status=generated）
        batch = await _tool('hasn.deck.page.write_batch').execute(
            ctx,
            {
                'deck_id': deck_id,
                'pages': [
                    {'position': 0, 'title': 'P0', 'html': _VALID_HTML},
                    {'position': 1, 'title': 'P1', 'html': _VALID_HTML},
                ],
            },
        )
        assert batch['written'] == 2
        assert batch['rejected'] == []

        # 4) 查详情：outline 形状 + 2 页
        got = await _tool('hasn.deck.get').execute(ctx, {'deck_id': deck_id})
        assert got['deck']['outline'] == {'items': [{'title': '封面'}, {'title': '要点'}]}
        assert len(got['pages']) == 2
        page_ids = [str(p['id']) for p in sorted(got['pages'], key=operator.itemgetter('position'))]

        # 5) 写一个不合格页 → 进 rejected，不落库
        bad = await _tool('hasn.deck.page.write_batch').execute(
            ctx, {'deck_id': deck_id, 'pages': [{'position': 2, 'title': 'X', 'html': _BAD_HTML}]}
        )
        assert bad['written'] == 0
        assert len(bad['rejected']) == 1 and bad['rejected'][0]['position'] == 2

        # 6) list 含它
        listed = await _tool('hasn.deck.list').execute(ctx, {})
        assert any(str(d['id']) == deck_id for d in listed['decks'])

        # 7) 重排：逆序两页 → 校验落位
        reordered = await _tool('hasn.deck.page.reorder').execute(
            ctx, {'deck_id': deck_id, 'page_ids': list(reversed(page_ids))}
        )
        positions = {str(p['id']): p['position'] for p in reordered['pages']}
        assert positions[page_ids[1]] == 0 and positions[page_ids[0]] == 1

        # 8) 编辑页标题
        edited = await _tool('hasn.deck.page.edit').execute(
            ctx, {'deck_id': deck_id, 'page_id': page_ids[0], 'title': '改后标题'}
        )
        assert edited['page']['title'] == '改后标题'

        # 9) 删一页 → 剩 1 页
        await _tool('hasn.deck.page.delete').execute(ctx, {'deck_id': deck_id, 'page_id': page_ids[0]})
        after = await _tool('hasn.deck.get').execute(ctx, {'deck_id': deck_id})
        assert len(after['pages']) == 1

        # 10) 样式：list 返回（内置 37 ∪ owner）；get 不存在 → 报错
        styles = await _tool('hasn.deck.style.list').execute(ctx, {})
        assert isinstance(styles['styles'], list)
        with pytest.raises(Exception):
            await _tool('hasn.deck.style.get').execute(ctx, {'style_id': f'no_such_{uuid.uuid4().hex[:8]}'})

        # 11) 删 deck → list 不再含它
        deleted = await _tool('hasn.deck.delete').execute(ctx, {'deck_id': deck_id})
        assert deleted['deleted'] is True
        listed2 = await _tool('hasn.deck.list').execute(ctx, {})
        assert not any(str(d['id']) == deck_id for d in listed2['decks'])
    finally:
        async with async_db_session.begin() as db:
            if deck_id:
                await db.execute(text('DELETE FROM hasn_deck.page WHERE deck_id = :d'), {'d': int(deck_id)})
            await db.execute(text('DELETE FROM hasn_deck.deck WHERE owner_id = :o'), {'o': owner})
            await db.execute(text('DELETE FROM hasn_sync_events WHERE owner_id = :o'), {'o': owner})
