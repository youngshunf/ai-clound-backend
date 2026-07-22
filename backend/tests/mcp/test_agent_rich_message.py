"""MEDIA-P3 — 分身富媒体消息工具真实单测（禁 mock）。

hasn.message.send 扩展为文本 + 图片/语音/文件（attachments）+ 信息卡片（card）。
- 纯逻辑：卡片构造（信息卡，零授权/工具动作）、缺 title 拒绝、非法 link 拒绝、schema 暴露。
- 活体 DB（本地 15432）：附件按资产注册表解析真实元数据 + content_type 推断 + 归属校验
  （他人资产拒绝）。无 DB 时跳过对应用例（不伪造）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_agent_rich_message.py
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.message import (
    MessageSendTool,
    _build_agent_card_body,
    _derive_name,
    _resolve_attachments,
    _resolve_to_target,
)

# ── 纯逻辑（无 DB）──────────────────────────────────────────


def test_schema_exposes_attachments_and_card_only_to_required() -> None:
    """新 schema 暴露 attachments + card；仅 to 必填（content 改可选）。"""
    schema = MessageSendTool().input_schema
    props = schema['properties']
    assert 'attachments' in props
    assert 'card' in props
    assert schema['required'] == ['to']


def test_derive_name_from_kind_and_mime() -> None:
    assert _derive_name('image', 'image/png') == 'image.png'
    assert _derive_name('voice', 'audio/mpeg') == 'voice.mp3'
    assert _derive_name('file', 'application/pdf') == 'file.pdf'
    # 未知 mime → 无扩展名，不伪造
    assert _derive_name('file', 'application/x-weird') == 'file'


def test_build_agent_card_body_is_informational_card() -> None:
    """分身卡片 = 信息卡：source.kind=agent、只 open_uri 主操作、无授权请求。"""
    body = _build_agent_card_body(
        'a_demo_agent',
        '小智',
        {
            'title': '本周进展',
            'description': '已完成 3 项',
            'fields': [{'label': '状态', 'value': '顺利'}],
            'link': {'label': '查看详情', 'uri': 'https://example.com/report'},
        },
    )
    assert body['schema_version'] == 'hasn.card/0.1'
    assert body['title'] == '本周进展'
    assert body['source']['kind'] == 'agent'
    assert body['source']['id'] == 'a_demo_agent'
    assert body['source']['display_name'] == '小智'
    assert body['fields'] == [{'label': '状态', 'value': '顺利'}]
    assert body['primary_action']['kind'] == 'open_uri'
    assert body['primary_action']['uri'] == 'https://example.com/report'
    # 信息卡绝不携带授权请求（安全）
    assert body['authorization_request'] is None


def test_build_agent_card_body_without_title_rejected() -> None:
    with pytest.raises(RuntimeError):
        _build_agent_card_body('a_x', '小智', {'description': '无标题'})


def test_build_agent_card_body_rejects_unsafe_link_scheme() -> None:
    """link.uri 仅允许 hasn/http(s)；ftp 等被卡片 schema 拒绝（防越权跳转）。"""
    from backend.common.exception import errors

    with pytest.raises((errors.RequestError, ValueError)):
        _build_agent_card_body('a_x', '小智', {'title': 'x', 'link': {'label': 'go', 'uri': 'ftp://evil/x'}})


def test_build_agent_card_body_no_link_has_no_primary_action() -> None:
    body = _build_agent_card_body('a_x', '小智', {'title': '纯文字卡片'})
    assert body['primary_action'] is None
    assert body['actions'] == []


# ── 主人寻址哨兵（"owner" → owner_hasn_id）──────────────────


def test_resolve_to_target_owner_sentinel() -> None:
    """保留值 "owner"/"@owner"（大小写、首尾空白无关）→ 解析为主人 hasn_id。"""
    assert _resolve_to_target('owner', 'h_owner_123') == 'h_owner_123'
    assert _resolve_to_target('@owner', 'h_owner_123') == 'h_owner_123'
    assert _resolve_to_target('  OWNER  ', 'h_owner_123') == 'h_owner_123'


def test_resolve_to_target_passthrough_non_sentinel() -> None:
    """非哨兵目标（HASN ID / 唤星号）原样透传，不受影响。"""
    assert _resolve_to_target('h_someone', 'h_owner_123') == 'h_someone'
    assert _resolve_to_target('a_friend_agent', 'h_owner_123') == 'a_friend_agent'
    assert _resolve_to_target('200001', 'h_owner_123') == '200001'


def test_resolve_to_target_owner_missing_rejected() -> None:
    """主人身份缺失时用 "owner" 哨兵 → 抛错不静默（避免误发到错误目标）。"""
    with pytest.raises(RuntimeError, match='主人身份'):
        _resolve_to_target('owner', None)


@pytest.mark.asyncio
async def test_message_send_rejects_missing_owner_identity() -> None:
    """主人身份缺失时，发送前必须拒绝，不能进入任意会话或联系人写路径。"""
    context = AgentContext(
        hasn_id='a_missing_owner',
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=None,
        session_uuid='amk_missing_owner',
    )
    with pytest.raises(RuntimeError, match='缺少 owner_hasn_id'):
        await MessageSendTool().execute(context, {'to': 'h_receiver', 'content': '你好'})


# ── 活体 DB（真实 hasn_assets 解析 + 归属校验）─────────────────
# 单 session 单连接覆盖全部 DB 断言：避免多用例各开 async session 触发 asyncpg +
# pytest-asyncio 跨用例连接清理竞态（pass/skip 交替的 flaky）。无 DB 时整体跳过（不伪造）。


def _new_asset(owner_hasn_id: str, kind: str, mime: str):
    from backend.app.hasn.model.hasn_assets import HasnAssets

    asset_id = 'ast_' + uuid.uuid4().hex
    return asset_id, HasnAssets(
        asset_id=asset_id,
        owner_hasn_id=owner_hasn_id,
        access='private',
        storage_id=0,
        object_key=f'test/{asset_id}',
        kind=kind,
        mime=mime,
        size_bytes=2048,
        width=1280 if kind == 'image' else None,
        height=720 if kind == 'image' else None,
        duration_ms=3400 if kind == 'voice' else None,
    )


@pytest.mark.asyncio
async def test_resolve_attachments_against_real_db() -> None:
    """真实 hasn_assets：图片/语音元数据解析 + content_type 推断 + 归属/缺失安全校验。

    全部断言在单个 async session 内完成（零 mock）。无 DB（DATABASE_PORT=15432）时跳过。
    """
    import sqlalchemy

    from sqlalchemy import delete

    from backend.app.hasn.model.hasn_assets import HasnAssets
    from backend.database.db import async_db_session

    try:
        async with async_db_session() as db:
            await db.execute(sqlalchemy.text('SELECT 1'))
    except Exception:
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')

    owner = 'h_rich_msg_owner'
    img_id, img = _new_asset(owner, 'image', 'image/png')
    voice_id, voice = _new_asset(owner, 'voice', 'audio/mpeg')
    foreign_id, foreign = _new_asset('h_owner_A', 'image', 'image/png')

    async with async_db_session() as db:
        db.add_all([img, voice, foreign])
        await db.flush()
        try:
            # 1) 图片 → content_type=2 + 真实 kind/mime/size/宽高
            attachments, content_type = await _resolve_attachments(db, owner, [f'hasn://asset/{img_id}'])
            assert content_type == 2
            att = attachments[0]
            assert att == {
                'uri': f'hasn://asset/{img_id}', 'kind': 'image', 'mime': 'image/png',
                'name': 'image.png', 'size': 2048, 'width': 1280, 'height': 720,
            }

            # 2) 语音 → content_type=4 + duration_ms
            v_atts, v_ct = await _resolve_attachments(db, owner, [f'hasn://asset/{voice_id}'])
            assert v_ct == 4
            assert v_atts[0]['duration_ms'] == 3400
            assert v_atts[0]['name'] == 'voice.mp3'

            # 3) 安全：他人资产 → 拒绝（防越权 grant）
            with pytest.raises(RuntimeError, match='不属于'):
                await _resolve_attachments(db, 'h_owner_B', [f'hasn://asset/{foreign_id}'])

            # 4) 资产不存在 → 拒绝不伪造
            with pytest.raises(RuntimeError, match='不存在'):
                await _resolve_attachments(db, owner, ['hasn://asset/ast_nonexistent_xyz'])
        finally:
            await db.execute(delete(HasnAssets).where(HasnAssets.asset_id.in_([img_id, voice_id, foreign_id])))
            await db.commit()
