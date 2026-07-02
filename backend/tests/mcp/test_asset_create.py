"""hasn.asset.create — 分身资产上传工具真实单测（禁 mock）。

补齐分身「发图/发附件」链路缺的中间一环：分身把自己的内容（自画 SVG/base64 图片/文本）
上传成 hasn://asset/{id}，再用 hasn.message.send(attachments=[...]) 发出去。

- 纯逻辑（无 DB/S3）：base64/text 解码、非法编码拒绝、kind 推断、文件名合成、可选整数、schema、超限拒绝。
- 活体 DB+S3（本地 15432 + 配置的 private 桶）：真实 execute 落桶注册 → asset_id/uri/归属/kind
  → 再经 message 工具 _resolve_attachments 证明「上传出来的资产能当附件发」。无 DB/无 private 桶时跳过（不伪造）：
    DATABASE_PORT=15432 pytest backend/tests/mcp/test_asset_create.py
"""

from __future__ import annotations

import base64

import pytest

from backend.app.mcp.tools.asset import (
    AssetCreateTool,
    decode_content,
    derive_filename,
    kind_of_mime,
)

# 1x1 透明 PNG（67 字节）——真实可解码的最小图片字节。
_TINY_PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


# ── 纯逻辑（无 DB）──────────────────────────────────────────


def test_kind_of_mime() -> None:
    assert kind_of_mime('image/png') == 'image'
    assert kind_of_mime('image/svg+xml') == 'image'
    assert kind_of_mime('audio/mpeg') == 'voice'
    assert kind_of_mime('application/pdf') == 'file'
    assert kind_of_mime('text/plain') == 'file'
    assert kind_of_mime('') == 'file'


def test_decode_content_base64_roundtrip() -> None:
    raw = b'\x89PNG\r\n\x1a\n hello bytes'
    encoded = base64.b64encode(raw).decode()
    assert decode_content(encoded, 'base64') == raw
    # 缺省即 base64
    assert decode_content(encoded, '') == raw


def test_decode_content_text_is_utf8() -> None:
    assert decode_content(_SVG, 'text') == _SVG.encode('utf-8')
    assert decode_content('中文 hi', 'utf8') == '中文 hi'.encode()


def test_decode_content_invalid_base64_rejected() -> None:
    with pytest.raises(RuntimeError, match='base64'):
        decode_content('not valid base64 @@@@', 'base64')


def test_decode_content_unknown_encoding_rejected() -> None:
    with pytest.raises(RuntimeError, match='不支持的 encoding'):
        decode_content('x', 'hex')


def test_derive_filename() -> None:
    assert derive_filename('my.png', 'image', 'image/png') == 'my.png'
    assert derive_filename(None, 'image', 'image/svg+xml') == 'image.svg'
    assert derive_filename('  ', 'voice', 'audio/mpeg') == 'voice.mp3'
    # 未知 mime → 无扩展名，不伪造
    assert derive_filename(None, 'file', 'application/x-weird') == 'file'


def test_schema_requires_content_and_mime() -> None:
    schema = AssetCreateTool().input_schema
    assert schema['required'] == ['content', 'mime']
    props = schema['properties']
    assert set(props) >= {'content', 'mime', 'encoding', 'filename', 'width', 'height', 'duration_ms'}
    assert props['encoding']['enum'] == ['base64', 'text']


def test_tool_identity_and_scope() -> None:
    tool = AssetCreateTool()
    assert tool.name == 'hasn.asset.create'
    assert tool.namespace == 'hasn.asset'
    assert tool.source == 'platform'
    assert tool.execution_location == 'cloud'
    assert tool.required_scopes == ['asset:create']


def _agent_context(owner_hasn_id: str | None):
    from backend.app.mcp.auth import AgentContext

    return AgentContext(
        hasn_id='a_asset_test_agent',
        owner_id=999001,
        agent_status='active',
        metadata={},
        agent_name='资产测试分身',
        owner_hasn_id=owner_hasn_id,
    )


@pytest.mark.asyncio
async def test_execute_missing_owner_rejected() -> None:
    """owner_hasn_id 缺失 → 拒绝（资产必须有归属，不静默）。无 DB 即触发（守卫在 DB 之前）。"""
    with pytest.raises(RuntimeError, match='主人身份'):
        await AssetCreateTool().execute(
            _agent_context(None), {'content': _SVG, 'mime': 'image/svg+xml', 'encoding': 'text'}
        )


@pytest.mark.asyncio
async def test_execute_oversize_rejected_before_upload() -> None:
    """超出 kind 限额 → 在落桶前就拒绝（守卫先于 DB/S3，无需活体环境）。"""
    from backend.app.mcp.tools.asset import _MAX_SIZE

    oversize = base64.b64encode(b'\x00' * (_MAX_SIZE['image'] + 1)).decode()  # 动态读图片限额，防限额调整后测试漂移
    with pytest.raises(RuntimeError, match='超出大小上限'):
        await AssetCreateTool().execute(_agent_context('h_asset_owner'), {'content': oversize, 'mime': 'image/png'})


# ── 活体 DB+S3（真实落桶注册 + 与 message 附件解析串联）─────────────
# 无 DB 或无 private 桶配置时整体跳过（不伪造）。


@pytest.mark.asyncio
async def test_asset_create_e2e_real_db_and_storage() -> None:
    """真实 execute：SVG(text) + PNG(base64) 两路落桶注册 → 归属/kind/uri，
    并经 message._resolve_attachments 证明产出的资产可作附件发送。"""
    import sqlalchemy

    from sqlalchemy import delete, select

    from backend.app.hasn.model import HasnArtifacts
    from backend.app.hasn.model.hasn_assets import HasnAssets
    from backend.app.mcp.tools.message import _resolve_attachments
    from backend.database.db import async_db_session
    from backend.plugin.s3.crud.storage import s3_storage_dao

    # 1) DB 可达？
    try:
        async with async_db_session() as db:
            await db.execute(sqlalchemy.text('SELECT 1'))
            # 2) 有 private 桶配置？（无则真实上传无处可写，诚实跳过不伪造）
            storages = await s3_storage_dao.get_all(db)
            has_private = any(getattr(s, 'access', 'private') == 'private' for s in storages)
    except Exception:
        pytest.skip('需活体 DB（DATABASE_PORT=15432）；无 DB 时跳过，不伪造')
    if not has_private:
        pytest.skip('未配置 private S3 存储；跳过真实上传，不伪造')

    owner = 'h_asset_create_owner'
    tool = AssetCreateTool()
    created_ids: list[str] = []
    try:
        # —— SVG 走 text 编码（分身自己写的图，免 base64）——
        svg_result = await tool.execute(
            _agent_context(owner),
            {'content': _SVG, 'mime': 'image/svg+xml', 'encoding': 'text', 'filename': 'diagram.svg'},
        )
        assert svg_result['asset_id'].startswith('ast_')
        assert svg_result['uri'] == f'hasn://asset/{svg_result["asset_id"]}'
        assert svg_result['kind'] == 'image'
        assert svg_result['mime'] == 'image/svg+xml'
        assert svg_result['size'] == len(_SVG.encode('utf-8'))
        created_ids.append(svg_result['asset_id'])

        # —— PNG 走 base64，宽高透传 ——
        png_result = await tool.execute(
            _agent_context(owner),
            {'content': _TINY_PNG_B64, 'mime': 'image/png', 'width': 1, 'height': 1},
        )
        assert png_result['kind'] == 'image'
        assert png_result['width'] == 1
        assert png_result['size'] == len(base64.b64decode(_TINY_PNG_B64))
        created_ids.append(png_result['asset_id'])

        # —— 落库归属 = owner（取自 Agent 凭证，非自报）——
        async with async_db_session() as db:
            # asset_id 是业务键（非主键 id BIGINT），按列查。
            row = (
                await db.execute(select(HasnAssets).where(HasnAssets.asset_id == svg_result['asset_id']))
            ).scalar_one_or_none()
            assert row is not None
            assert row.owner_hasn_id == owner
            assert row.access == 'private'

            # —— 产出的资产能当附件发：message._resolve_attachments 真实解析通过 ——
            attachments, content_type = await _resolve_attachments(db, owner, [svg_result['uri']])
            assert content_type == 2  # image
            assert attachments[0]['uri'] == svg_result['uri']
            assert attachments[0]['kind'] == 'image'

            # —— 安全：他人 owner 解析同一资产被拒（归属隔离）——
            with pytest.raises(RuntimeError, match='不属于'):
                await _resolve_attachments(db, 'h_other_owner', [svg_result['uri']])

            # —— 产物自动登记：上传成功即在 hasn_artifacts 落一条（source_kind='upload'）——
            artifact = (
                await db.execute(
                    select(HasnArtifacts).where(HasnArtifacts.asset_id == svg_result['asset_id'])
                )
            ).scalar_one_or_none()
            assert artifact is not None, '上传的资产应自动登记到产物表'
            assert artifact.agent_hasn_id == 'a_asset_test_agent'  # 身份取自 Agent 凭证
            assert artifact.owner_hasn_id == owner
            assert artifact.kind == 'image'
            assert artifact.source_kind == 'upload'
            assert artifact.source_tool == 'hasn.asset.create'
            assert artifact.title == 'diagram.svg'
            assert artifact.status == 'active'
            # metadata 快照冗余 mime/size，便于离线展示
            assert artifact.meta_data['mime'] == 'image/svg+xml'
            assert artifact.meta_data['size'] == len(_SVG.encode('utf-8'))

            # PNG 上传也各自登记一条（宽高进 metadata）
            png_artifact = (
                await db.execute(
                    select(HasnArtifacts).where(HasnArtifacts.asset_id == png_result['asset_id'])
                )
            ).scalar_one_or_none()
            assert png_artifact is not None
            assert png_artifact.source_kind == 'upload'
            assert png_artifact.meta_data.get('width') == 1
            assert png_artifact.meta_data.get('height') == 1
    finally:
        if created_ids:
            async with async_db_session() as db:
                await db.execute(delete(HasnArtifacts).where(HasnArtifacts.asset_id.in_(created_ids)))
                await db.execute(delete(HasnAssets).where(HasnAssets.asset_id.in_(created_ids)))
                await db.commit()
