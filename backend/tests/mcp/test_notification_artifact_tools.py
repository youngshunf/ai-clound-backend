"""平台工具 · notification / artifact 域 真实 service 测试（禁 mock，TOOLMIG-3）。

验证从 hasn-node 本地 hasn-mcp 迁来的两个「纯云端代理」工具：
- `hasn.notifications.emit`（统一通知 §7）
- `hasn.artifact.record`（产物化 P6）

契约（无需 DB）：工具名/命名空间/execution_location/scope 与原 hasn-mcp 工具 1:1；
input_schema 必填项 + 入参校验（三选一 / 必填）防回归。
真实 PG 往返：artifact.record 文本产物真落库（事务真提交，测试后清理该 owner 行）；
notification.emit 对未授权 App 真打 registry → ForbiddenError（证明 service 接线，无需 seed manifest）。

需活体 DB（本地 15432）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_notification_artifact_tools.py
无 DB 时跳过真实往返（不伪造）。
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.artifact import ARTIFACT_TOOLS, ArtifactRecordTool
from backend.app.mcp.tools.notification import NOTIFICATION_TOOLS, NotificationEmitTool


def _agent_ctx(owner_hasn_id: str, agent_hasn_id: str = 'a_notif_artifact_test') -> AgentContext:
    return AgentContext(
        hasn_id=agent_hasn_id,
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner_hasn_id,
        session_uuid='amk_notif_artifact_test',
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


# ── 契约（无需 DB）────────────────────────────────────────────────────────────
def test_tools_register_single_each() -> None:
    """各域恰好 1 个工具，名/命名空间正确。"""
    assert [t.name for t in NOTIFICATION_TOOLS] == ['hasn.notifications.emit']
    assert [t.name for t in ARTIFACT_TOOLS] == ['hasn.artifact.record']


def test_tools_are_cloud_platform() -> None:
    """两工具 source=platform、execution_location=cloud、命名空间正确。"""
    notif = NOTIFICATION_TOOLS[0]
    assert notif.source == 'platform'
    assert notif.namespace == 'hasn.notifications'
    assert notif.execution_location == 'cloud'

    art = ARTIFACT_TOOLS[0]
    assert art.source == 'platform'
    assert art.namespace == 'hasn.artifact'
    assert art.execution_location == 'cloud'


def test_tools_declare_no_capability_scope() -> None:
    """与本地 hasn-mcp 工具 1:1：不声明独立 capability scope（服务层是真权威）。"""
    assert NOTIFICATION_TOOLS[0].required_scopes == []
    assert ARTIFACT_TOOLS[0].required_scopes == []


def test_required_fields_match_contract() -> None:
    """必填项与原 hasn-mcp 工具逐字段一致。"""
    assert NOTIFICATION_TOOLS[0].input_schema['required'] == ['app_id', 'category', 'type', 'title']
    assert ARTIFACT_TOOLS[0].input_schema['required'] == ['kind']


def test_notification_category_and_priority_enums() -> None:
    """category / priority 枚举与原工具一致，防漂移。"""
    props = NOTIFICATION_TOOLS[0].input_schema['properties']
    assert props['category']['enum'] == ['app', 'commerce', 'reminder', 'system']
    assert props['priority']['enum'] == ['critical', 'high', 'normal', 'low']


def test_artifact_kind_and_source_kind_enums() -> None:
    """kind / source_kind 枚举与原工具一致（含 video，P6）。"""
    props = ARTIFACT_TOOLS[0].input_schema['properties']
    assert props['kind']['enum'] == [
        'image', 'voice', 'video', 'file', 'document', 'deck', 'webpage', 'dataset', 'other'
    ]
    assert props['source_kind']['enum'] == ['task_result', 'tool_output', 'upload', 'external']


@pytest.mark.asyncio(loop_scope='module')
async def test_notification_emit_rejects_missing_required() -> None:
    """缺必填（app_id）→ RuntimeError（校验在打 DB 前，无需活体库）。"""
    with pytest.raises(RuntimeError, match='app_id'):
        await NotificationEmitTool().execute(
            _agent_ctx('h_x'), {'category': 'app', 'type': 't', 'title': 'x'}
        )


@pytest.mark.asyncio(loop_scope='module')
async def test_artifact_record_rejects_missing_kind() -> None:
    """缺 kind → RuntimeError（校验在打 DB 前）。"""
    with pytest.raises(RuntimeError, match='kind'):
        await ArtifactRecordTool().execute(_agent_ctx('h_x'), {'body': 'x'})


@pytest.mark.asyncio(loop_scope='module')
async def test_artifact_record_requires_one_body_asset_resource() -> None:
    """kind 有但 body/asset_id/resource_uri 全缺 → RuntimeError（三选一，校验在打 DB 前）。"""
    with pytest.raises(RuntimeError, match='body'):
        await ArtifactRecordTool().execute(_agent_ctx('h_x'), {'kind': 'document'})


# ── 真实 PG 往返 ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio(loop_scope='module')
async def test_artifact_record_text_roundtrip_real_db() -> None:
    """真实 PG：record 文本产物（kind=document + body + origin_ref）→ 落库可查；测试后清理。"""
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from sqlalchemy import delete, select

    from backend.app.hasn.model import HasnArtifacts
    from backend.database.db import async_db_session

    owner = f'h_artifact_tool_{uuid.uuid4().hex[:16]}'
    ctx = _agent_ctx(owner)
    try:
        res = await ArtifactRecordTool().execute(
            ctx,
            {
                'kind': 'document',
                'title': '竞品调研报告',
                'body': '# 竞品调研\n\n## 市场概览\n…结论可执行',
                'origin_ref': 'resource:plan:todo:42',
            },
        )
        artifact_id = res['artifact_id']
        assert artifact_id

        async with async_db_session() as db:
            row = (
                await db.execute(select(HasnArtifacts).where(HasnArtifacts.artifact_id == artifact_id))
            ).scalar_one()
            assert row.owner_hasn_id == owner
            assert row.agent_hasn_id == ctx.agent_hasn_id
            assert row.kind == 'document'
            assert row.body.startswith('# 竞品调研')
            assert row.origin_ref == 'resource:plan:todo:42'
            # 显式登记默认归 task_result，并自带 source_tool。
            assert row.source_kind == 'task_result'
            assert row.source_tool == 'hasn.artifact.record'
            assert row.status == 'active'
    finally:
        async with async_db_session.begin() as db:
            await db.execute(delete(HasnArtifacts).where(HasnArtifacts.owner_hasn_id == owner))


@pytest.mark.asyncio(loop_scope='module')
async def test_notification_emit_unauthorized_app_forbidden_real_db() -> None:
    """真实 PG：未发布/未声明 emit 的 App → service registry 返回 None → ForbiddenError。

    证明工具真接 notification_service.app_emit（service 是授权权威），无需 seed manifest。
    """
    if not await _db_reachable():
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    from backend.common.exception.errors import ForbiddenError

    owner = f'h_notif_tool_{uuid.uuid4().hex[:16]}'
    with pytest.raises(ForbiddenError):
        await NotificationEmitTool().execute(
            _agent_ctx(owner),
            {
                'app_id': f'no_such_app_{uuid.uuid4().hex[:8]}',
                'category': 'app',
                'type': 'post_featured',
                'title': '你的帖子被加精了',
            },
        )
