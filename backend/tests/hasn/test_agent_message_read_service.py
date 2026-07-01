"""AgentMessageReadService 真实 PG 集成测试（MSGTOOL-3，零 mock）。

零 mock：用真实本地 PostgreSQL(15432) 跑 list_messages / list_conversations / search_messages
全链路（uuid cast + jsonb ->> + ILIKE ESCAPE 都是 PG 特性，SQLite 跑不了）。
每个用例把种子行 INSERT 进**未提交事务**，同事务内查询可见，async_db_session 退出即回滚——
不落库、不污染既有消息（对齐 test_platform_default_config 的事务内验收范式）。

覆盖：
- 默认倒序（最新在前）+ keyset 翻页（cursor 无重叠、后页更早）
- conversation_id 过滤（会话详情）
- 会话列表按最后活动倒序 + 每会话取最新一条预览
- 关键词搜索命中文本 body.text 与卡片 title/description，不误命中结构键（attachments 等）
- 通配符转义（`%`/`_` 当字面量，不当通配）
- owner 隔离（绝不串 owner）+ 空 query 直返空

需要：export DATABASE_PORT=15432（本地 huanxing 库）。
"""

from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa

from backend.app.hasn.service.agent_message_read_service import agent_message_read_service as svc
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

# 合成 owner，绝不与真实数据撞（h_ 前缀但明显是测试串）。
_OWNER = 'h_msgtool_test_owner_x'
_OTHER = 'h_msgtool_test_owner_y'


async def _seed(db, owner_id: str, conversation_id: str, content: dict, content_type: int = 1) -> None:
    """在当前（未提交）事务里插一条 hasn_messages 行；id 自增 → 插入顺序即 id 升序。"""
    await db.execute(
        sa.text(
            """
            INSERT INTO public.hasn_messages
                (conversation_id, owner_id, hasn_id, from_id, sender_hasn_id,
                 from_type, to_id, to_type, content_type, content, msg_type, status, created_time)
            VALUES
                (CAST(:conv AS uuid), :owner, :owner, :from_id, :from_id,
                 2, :owner, 1, :ct, CAST(:content AS jsonb), 'message', 1, now())
            """
        ),
        {
            'conv': conversation_id,
            'owner': owner_id,
            'from_id': 'a_msgtool_sender',
            'ct': content_type,
            'content': json.dumps(content, ensure_ascii=False),
        },
    )


def _uuid() -> str:
    return str(uuid.uuid4())


async def test_list_messages_desc_and_keyset_pagination() -> None:
    async with async_db_session() as db:
        conv_a, conv_b = _uuid(), _uuid()
        # 插入顺序 = 时间顺序（id 升序）：m1..m5
        await _seed(db, _OWNER, conv_a, {'text': 'm1 最早'})
        await _seed(db, _OWNER, conv_b, {'text': 'm2'})
        await _seed(db, _OWNER, conv_a, {'text': 'm3'})
        await _seed(db, _OWNER, conv_b, {'text': 'm4'})
        await _seed(db, _OWNER, conv_a, {'text': 'm5 最新'})

        page1 = await svc.list_messages(db, _OWNER, limit=3)
        assert [m['content'] for m in page1['messages']] == ['m5 最新', 'm4', 'm3'], '默认倒序=最新在前'
        assert page1['has_more'] is True
        assert page1['next_cursor'] is not None

        page2 = await svc.list_messages(db, _OWNER, limit=3, cursor=page1['next_cursor'])
        assert [m['content'] for m in page2['messages']] == ['m2', 'm1 最早'], 'keyset 翻页拿到更早的'
        assert page2['has_more'] is False
        ids1 = {m['message_id'] for m in page1['messages']}
        ids2 = {m['message_id'] for m in page2['messages']}
        assert ids1.isdisjoint(ids2), '两页无重叠（游标真生效，非每次从头）'


async def test_list_messages_conversation_filter() -> None:
    async with async_db_session() as db:
        conv_a, conv_b = _uuid(), _uuid()
        await _seed(db, _OWNER, conv_a, {'text': 'a-1'})
        await _seed(db, _OWNER, conv_b, {'text': 'b-1'})
        await _seed(db, _OWNER, conv_a, {'text': 'a-2'})

        res = await svc.list_messages(db, _OWNER, conversation_id=conv_a, limit=20)
        assert [m['content'] for m in res['messages']] == ['a-2', 'a-1'], '只返回该会话且倒序'
        assert all(m['conversation_id'] == conv_a for m in res['messages'])


async def test_list_conversations_aggregates_and_desc() -> None:
    async with async_db_session() as db:
        conv_a, conv_b = _uuid(), _uuid()
        await _seed(db, _OWNER, conv_a, {'text': 'a-old'})
        await _seed(db, _OWNER, conv_b, {'text': 'b-old'})
        await _seed(db, _OWNER, conv_a, {'text': 'a-latest'})  # conv_a 最后活动最新

        res = await svc.list_conversations(db, _OWNER, limit=20)
        convs = res['conversations']
        assert len(convs) == 2, '两个会话各一行（按会话聚合）'
        assert convs[0]['conversation_id'] == conv_a, '最近活动的会话排最前'
        assert convs[0]['last_message'] == 'a-latest', '每会话取最新一条作预览'
        assert convs[1]['conversation_id'] == conv_b


async def test_search_matches_text_and_card_not_structural_keys() -> None:
    async with async_db_session() as db:
        conv = _uuid()
        await _seed(db, _OWNER, conv, {'text': '明天下午开会提醒'})
        await _seed(db, _OWNER, conv, {'title': '本周周报', 'description': '含一张封面图片'}, content_type=5)
        await _seed(db, _OWNER, conv, {'text': '无关内容', 'attachments': [{'kind': 'image'}]})

        hit_text = await svc.search_messages(db, _OWNER, '开会', limit=20)
        assert [m['content'] for m in hit_text['messages']] == ['明天下午开会提醒']

        hit_card = await svc.search_messages(db, _OWNER, '图片', limit=20)
        assert len(hit_card['messages']) == 1
        assert '封面图片' in hit_card['messages'][0]['content']

        # 'attachments'/'kind' 是结构键，不在 text/title/description 里 → 不该命中
        hit_struct = await svc.search_messages(db, _OWNER, 'attachments', limit=20)
        assert hit_struct['messages'] == [], '不做全 jsonb 结构匹配，结构键不误命中'


async def test_search_escapes_wildcards() -> None:
    async with async_db_session() as db:
        conv = _uuid()
        await _seed(db, _OWNER, conv, {'text': 'a_b 字面下划线'})
        await _seed(db, _OWNER, conv, {'text': 'axb 任意字符'})

        res = await svc.search_messages(db, _OWNER, 'a_b', limit=20)
        contents = [m['content'] for m in res['messages']]
        assert 'a_b 字面下划线' in contents
        assert 'axb 任意字符' not in contents, '下划线被转义为字面量，不当 LIKE 通配'


async def test_owner_isolation_and_empty_query() -> None:
    async with async_db_session() as db:
        conv = _uuid()
        await _seed(db, _OWNER, conv, {'text': '属于 X 的消息'})
        await _seed(db, _OTHER, _uuid(), {'text': '属于 Y 的消息'})

        res = await svc.list_messages(db, _OWNER, limit=50)
        assert all('Y' not in m['content'] for m in res['messages']), 'owner 隔离：绝不串 owner'
        assert any('X' in m['content'] for m in res['messages'])

        empty = await svc.search_messages(db, _OWNER, '   ', limit=20)
        assert empty['messages'] == [] and empty['has_more'] is False, '空/纯空白 query 直返空'
