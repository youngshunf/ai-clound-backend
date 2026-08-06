"""doc19 §4.6 / D-20 · 云端 `owner_memory.owner_edited` 置位与复位的真实 PG 验收（零 mock）。

设计事实源：``docs/产品与技术/技术设计/02-平台能力/记忆与知识库/01-记忆领域与数据权威.md``
  覆盖主人直接写入与画像重算边界

这一位是「主人手改过档案正文，下一轮重算必须保留其表述」的**唯一开关**，正反两个方向错了都
只表现为静默的记忆退化，没有任何报错：

- **该置不置** → 重算 prompt 不携带主人版本，手工编辑被下一轮合并静默冲掉（§4.6 明令禁止）；
- **不该置却置了**（MEMPUSH / 合并写回 / 系统兜底改写也置位）→ 每轮合并后都被误标成「主人改
  过」，强调段永久携带且**再也复位不掉**，档案被一版旧正文钉死。

故本文件按「谁在写 `user_md`」逐条钉：

1. 主人手工写（Owner JWT PATCH 的 `user_md` 键）→ 置位，且 `version` / `content` 一个都不动；
2. 清空 USER.md / 只改别的字段 → **不**置位；
3. MEMPUSH 写回（合并 apply 覆盖全部分身 `user_md`）→ **不**置位；
4. 系统兜底改写（昵称刷新重写 `称呼:` 行）→ **不**置位；
5. 合并 apply 带 `owner_memory` 键（真的重算了正文）→ 复位；
6. 合并 apply **缺** `owner_memory` 键（本轮没重算）→ 保持置位（关键回归）；
7. `merge/status` 把这一位透出给主人（跨端可见：手改发生在哪台设备只有那台本地知道）。

需本地 PostgreSQL :15432（不可达则跳过）。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.schema.hasn_agents import UpdateAgentProfileRequest
from backend.app.hasn.service.hasn_agents_service import agent_profile_service
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_memory.model import HasnOwnerMemory
from backend.app.hasn_memory.schema.merge_gate import MergeApplyRequest, MergeOwnerMemoryPayload
from backend.app.hasn_memory.service.merge_gate_service import merge_gate_service
from backend.app.hasn_memory.service.owner_memory_service import owner_memory_service
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.database.schema_names import SCHEMA_NAMES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

_SYNC_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')

#: 建档模板渲染出来的 USER.md 首行形态（`称呼:` 行是昵称刷新的作用点）。
_SEEDED_USER_MD = '称呼: 186****2019\n§\nOwner HASN ID: {owner_id}'


class Fixture:
    """一个主人 + 一个主脑分身（role='primary' 绑在 NODE_A），全部落真库。"""

    def __init__(self) -> None:
        marker = uuid.uuid4().hex
        self.owner_id = f'h_oe{marker[:20]}'
        self.agent_id = f'a_oe{marker[:20]}'
        self.node_id = f'node_{marker[:12]}'


@pytest_asyncio.fixture
async def env() -> AsyncIterator[tuple[AsyncSession, Fixture]]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    fx = Fixture()
    async with sessions.begin() as setup:
        setup.add_all([
            HasnHumans(
                hasn_id=fx.owner_id,
                star_id=f'h{uuid.uuid4().hex[:24]}',
                nickname='直编主人',
                status='active',
            ),
            HasnAgents(
                hasn_id=fx.agent_id,
                star_id=f'a{uuid.uuid4().hex[:24]}',
                owner_id=fx.owner_id,
                display_name=f'主脑分身{uuid.uuid4().hex[:6]}',
                agent_name=f'master{uuid.uuid4().hex[:8]}',
                role='primary',
                status='active',
                binding_node_id=fx.node_id,
                user_md=_SEEDED_USER_MD.format(owner_id=fx.owner_id),
            ),
        ])

    session = sessions()
    try:
        yield session, fx
    finally:
        await session.rollback()
        await session.close()
        async with sessions.begin() as cleanup:
            for stmt in (
                sa.text('DELETE FROM hasn_memory.semantic_fact WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.peer_portrait WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.merge_run WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.merge_request WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_memory.owner_memory WHERE owner_id = :o'),
                sa.text(f'DELETE FROM {_SYNC_EVENTS} WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_agents WHERE owner_id = :o'),
                sa.text('DELETE FROM hasn_humans WHERE hasn_id = :o'),
            ):
                await cleanup.execute(stmt, {'o': fx.owner_id})
        await engine.dispose()
        # 全局 async_db_session 池绑上一事件循环；每测试后释放（与合并闸测试同口径）。
        await async_engine.dispose()


async def _owner_memory(db: AsyncSession, owner_id: str) -> HasnOwnerMemory | None:
    """现查 `owner_memory` 行；合并闸走裸 SQL，必须先 expire 掉 ORM identity map 里的旧值。"""
    db.expire_all()
    return (
        await db.execute(select(HasnOwnerMemory).where(HasnOwnerMemory.owner_id == owner_id))
    ).scalar_one_or_none()


async def _owner_edited(db: AsyncSession, owner_id: str) -> bool:
    row = await _owner_memory(db, owner_id)
    return bool(row is not None and row.owner_edited)


async def _agent_user_md(db: AsyncSession, agent_id: str) -> str | None:
    db.expire_all()
    return (
        await db.execute(sa.select(HasnAgents.user_md).where(HasnAgents.hasn_id == agent_id))
    ).scalar_one_or_none()


async def _owner_writes_user_md(db: AsyncSession, fx: Fixture, content: str) -> None:
    """主人手工写：Owner JWT 的 `PATCH /api/v1/hasn/app/agents/by-hasn-id/{id}` 的 service 入口。

    daemon `PUT /api/v1/agents/{id}/memory-files/user`（记忆 tab 点保存）打的就是这条路。
    """
    await agent_profile_service.update_profile_cloud_first(
        db,
        owner_id=fx.owner_id,
        hasn_id=fx.agent_id,
        request=UpdateAgentProfileRequest(user_md=content),
    )


def _merge_body(
    fx: Fixture,
    *,
    base_version: int,
    content: str | None,
    base_owner_memory_edited: bool = False,
) -> MergeApplyRequest:
    """一轮合并提交体；``content=None`` 时**整个省略** `owner_memory` 键（与本地构造点同形）。"""
    payload: dict[str, Any] = {
        'run_id': f'mrun_{uuid.uuid4().hex[:24]}',
        'node_id': fx.node_id,
        'base_owner_memory_version': base_version,
        'base_owner_memory_edited': base_owner_memory_edited,
        'verdicts': [],
        'derived_facts': [],
        'peer_portraits': [],
        'summary': '本轮整理结果',
        'stats': {'facts_judged': 1, 'facts_merged': 0, 'facts_disputed': 0},
    }
    if content is not None:
        payload['owner_memory'] = MergeOwnerMemoryPayload(content=content)
    return MergeApplyRequest.model_validate(payload)


# --------------------------------------------------------------------------------------
# 1 · 主人手工写 → 置位（且不动 CAS 基线）
# --------------------------------------------------------------------------------------


async def test_owner_manual_user_md_write_marks_owner_edited(env: tuple[AsyncSession, Fixture]) -> None:
    """§4.6 逃生口：主人直改 USER.md → 云端 `owner_edited=true`，跨端可见。"""
    db, fx = env
    assert await _owner_memory(db, fx.owner_id) is None, '前置：本 owner 还没有 owner_memory 行'

    await _owner_writes_user_md(db, fx, '称呼: 老板\n§\n口味: 只喝冰美式，别再问了')
    await db.flush()

    row = await _owner_memory(db, fx.owner_id)
    assert row is not None, '主人手工写必须建出 owner_memory 行来承载标位'
    assert row.owner_edited is True
    assert await _agent_user_md(db, fx.agent_id) == '称呼: 老板\n§\n口味: 只喝冰美式，别再问了'


async def test_owner_manual_write_does_not_move_merge_baseline(env: tuple[AsyncSession, Fixture]) -> None:
    """置位**绝不动 `version` / `content`**：version 是主脑提交合并闸的 CAS 基线。

    基线取自主脑那台设备的本地 `owner_portraits.version`（`hasn-mcp::compute_plan`）。云端这里
    一动，主脑下一轮提交必然 409 `version_conflict`，而症状只表现成「主脑很久没整理了」。
    """
    db, fx = env
    await _owner_writes_user_md(db, fx, '口味: 只喝冰美式')
    await db.flush()

    row = await _owner_memory(db, fx.owner_id)
    assert row is not None
    assert row.version == 0, '建行分支必须给 version=0（= 尚未合并过），与本地建行值同口径'
    assert row.content is None, '云端 content 是合并态，手改正文的权威副本在 hasn_agents.user_md'

    # 首轮合并仍以 0 为合法基线 → 不被判 version_conflict。
    result = await merge_gate_service.apply(
        db,
        owner_id=fx.owner_id,
        agent_id=fx.agent_id,
        body=_merge_body(
            fx,
            base_version=0,
            content='健康: 主人注重抗衰老',
            base_owner_memory_edited=True,
        ),
    )
    assert result.applied is True
    assert result.new_owner_memory_version == 1


async def test_agent_owner_memory_snapshot_exposes_cas_state(env: tuple[AsyncSession, Fixture]) -> None:
    """Agent 读面必须同时给出 version 与主人直编标记，主脑才能基于最新权威快照重算。"""
    db, fx = env
    await _owner_writes_user_md(db, fx, '称呼: 老板\n§\n偏好: 保留主人原话')
    await db.flush()

    snapshot = await owner_memory_service.get_owner_memory(db, owner_id=fx.owner_id)
    assert snapshot == {
        'content': None,
        'version': 0,
        'owner_edited': True,
    }


async def test_clearing_user_md_does_not_mark_owner_edited(env: tuple[AsyncSession, Fixture]) -> None:
    """清空 USER.md 的语义是「让下一轮从事实重算」，没有要保留的主人表述 → 不置位。

    与本地 `mark_owner_portrait_edited` 跳过空正文同口径（本地那侧还有 `length > 0` 的 CHECK）。
    """
    db, fx = env
    await _owner_writes_user_md(db, fx, '   ')
    await db.flush()

    assert await _owner_edited(db, fx.owner_id) is False
    assert await _agent_user_md(db, fx.agent_id) == '   ', '清空动作本身照常落库（partial 语义不变）'


async def test_editing_other_profile_fields_does_not_mark_owner_edited(env: tuple[AsyncSession, Fixture]) -> None:
    """同一端点改 soul/memory/display_name 不是「改主人档案」→ 不置位。"""
    db, fx = env
    await agent_profile_service.update_profile_cloud_first(
        db,
        owner_id=fx.owner_id,
        hasn_id=fx.agent_id,
        request=UpdateAgentProfileRequest(soul_md='# 新人设', memory_md='# 新笔记', description='新简介'),
    )
    await db.flush()

    assert await _owner_edited(db, fx.owner_id) is False


# --------------------------------------------------------------------------------------
# 2 · 系统写入路径 → 一律不置位
# --------------------------------------------------------------------------------------


async def test_mempush_writeback_does_not_mark_owner_edited(env: tuple[AsyncSession, Fixture]) -> None:
    """MEMPUSH：合并 apply 用一条 bulk UPDATE 覆盖全部分身 `user_md` → 不得置位。

    它若也走置位路径，`_apply_owner_memory` 里「先复位、再 MEMPUSH」的顺序会让每一轮合并都以
    `owner_edited=true` 收尾——标位从此永远复位不掉，重算 prompt 永久携带强调段。
    """
    db, fx = env
    seeded = await _agent_user_md(db, fx.agent_id)

    result = await merge_gate_service.apply(
        db,
        owner_id=fx.owner_id,
        agent_id=fx.agent_id,
        body=_merge_body(fx, base_version=0, content='健康: 主人注重抗衰老'),
    )
    assert result.applied is True

    pushed = await _agent_user_md(db, fx.agent_id)
    assert pushed != seeded and pushed is not None and '抗衰老' in pushed, 'MEMPUSH 确实改写了 user_md'
    assert await _owner_edited(db, fx.owner_id) is False, 'MEMPUSH 写回不是主人手工写，不得置位'


async def test_system_nickname_refresh_does_not_mark_owner_edited(env: tuple[AsyncSession, Fixture]) -> None:
    """系统兜底改写：昵称刷新重写 USER.md 的 `称呼:` 行 → 不得置位。

    这条路径改的是建档时烙进的手机号掩码，不是主人的表述；置位会让每次改昵称都伪装成「主人手
    工改过档案」。
    """
    db, fx = env
    await agent_profile_service.refresh_seeded_agent_display_names(
        db,
        owner_id=fx.owner_id,
        current_nickname='福仔',
        previous_nickname=None,
    )
    await db.flush()

    refreshed = await _agent_user_md(db, fx.agent_id)
    assert refreshed is not None and '称呼: 福仔' in refreshed, '前置：昵称刷新确实改写了 user_md'
    assert await _owner_edited(db, fx.owner_id) is False


# --------------------------------------------------------------------------------------
# 3 · 复位：只有本轮真的重算了正文才复位
# --------------------------------------------------------------------------------------


async def test_merge_apply_with_owner_memory_resets_owner_edited(env: tuple[AsyncSession, Fixture]) -> None:
    """带 `owner_memory` 键 = 本轮真的重算并消费了主人手工版本 → 复位。"""
    db, fx = env
    await _owner_writes_user_md(db, fx, '口味: 只喝冰美式，别再问了')
    await db.flush()
    assert await _owner_edited(db, fx.owner_id) is True

    result = await merge_gate_service.apply(
        db,
        owner_id=fx.owner_id,
        agent_id=fx.agent_id,
        body=_merge_body(
            fx,
            base_version=0,
            content='口味: 只喝冰美式（主人手工确认）',
            base_owner_memory_edited=True,
        ),
    )
    assert result.applied is True

    row = await _owner_memory(db, fx.owner_id)
    assert row is not None
    assert row.owner_edited is False, '手工版本已被本轮重算吸收，标位必须复位'
    assert row.version == 1
    assert row.content is not None and '冰美式' in row.content


async def test_merge_apply_without_owner_memory_keeps_owner_edited(env: tuple[AsyncSession, Fixture]) -> None:
    """**关键回归**：缺 `owner_memory` 键的轮次不得复位。

    本地 `hasn.memory.merge` 未重算画像时会整个省略这个键（零 fake）。跟着复位等于宣称「主人
    的手改已被吸收」——下一轮 prompt 不再携带主人版本，一轮空合并就把手工编辑抹掉了。
    """
    db, fx = env
    await _owner_writes_user_md(db, fx, '口味: 只喝冰美式，别再问了')
    await db.flush()
    assert await _owner_edited(db, fx.owner_id) is True

    body = _merge_body(fx, base_version=0, content=None, base_owner_memory_edited=True)
    assert body.owner_memory is None, '缺键必须解析成 None，而不是 422'
    result = await merge_gate_service.apply(db, owner_id=fx.owner_id, agent_id=fx.agent_id, body=body)
    assert result.applied is True
    assert result.new_owner_memory_version == 1, '轮次水位照常推进'

    row = await _owner_memory(db, fx.owner_id)
    assert row is not None
    assert row.owner_edited is True, '本轮没重算画像，主人手工标位必须原样保留'


async def test_merge_apply_with_blank_owner_memory_keeps_owner_edited(env: tuple[AsyncSession, Fixture]) -> None:
    """空白正文与缺键同义（都没重算出可用画像）→ 同样不得复位。"""
    db, fx = env
    await _owner_writes_user_md(db, fx, '口味: 只喝冰美式，别再问了')
    await db.flush()

    result = await merge_gate_service.apply(
        db,
        owner_id=fx.owner_id,
        agent_id=fx.agent_id,
        body=_merge_body(
            fx,
            base_version=0,
            content='   ',
            base_owner_memory_edited=True,
        ),
    )
    assert result.applied is True

    row = await _owner_memory(db, fx.owner_id)
    assert row is not None
    assert row.owner_edited is True


# --------------------------------------------------------------------------------------
# 4 · 跨端可见：merge/status 透出这一位
# --------------------------------------------------------------------------------------


async def test_merge_status_exposes_owner_memory_edited(env: tuple[AsyncSession, Fixture]) -> None:
    """§5.5 可见性面必须带 `owner_memory_edited`：主人换一台设备也要看得到「你手工改过」。

    手改发生在哪台设备只有那台的本地 `owner_portraits` 知道，云端这一位是唯一的跨端事实源。
    """
    db, fx = env
    before = await merge_gate_service.merge_status(db, owner_id=fx.owner_id)
    assert before.owner_memory_edited is False, '没手改过就是 False，不猜'
    assert before.owner_memory_version == 0

    await _owner_writes_user_md(db, fx, '口味: 只喝冰美式，别再问了')
    await db.flush()

    edited = await merge_gate_service.merge_status(db, owner_id=fx.owner_id)
    assert edited.owner_memory_edited is True
    assert edited.owner_memory_version == 0, '手改不推进轮次水位（CAS 基线不动）'
    assert 'owner_memory_edited' in edited.model_dump(), 'DTO 必须真的把这一位序列化出去'

    await merge_gate_service.apply(
        db,
        owner_id=fx.owner_id,
        agent_id=fx.agent_id,
        body=_merge_body(
            fx,
            base_version=0,
            content='口味: 只喝冰美式（已并入）',
            base_owner_memory_edited=True,
        ),
    )
    merged = await merge_gate_service.merge_status(db, owner_id=fx.owner_id)
    assert merged.owner_memory_edited is False, '重算消费后提示随即消失'
    assert merged.owner_memory_version == 1
