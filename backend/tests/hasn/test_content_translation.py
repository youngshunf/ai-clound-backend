"""用户内容按需翻译（轨道 B P4）云端验收 —— 真实 PG + 真实 Redis，零 mock。

施工清单「必测用例」七条全在这里：

1. 缓存命中：同资源同语言二次请求 `cached: true`，无新增 LLM 调用
2. 原文变更失效：改帖子内容 → `source_hash` 变 → 重新翻译，不返回旧译文
3. 判权：无权限用户翻不可见帖子 → 403
4. 并发首译：10 个并发请求同一 key → 只有 1 次 LLM 调用
5. 结构保留：含代码块/URL/@mention/`hasn://` URI 的帖子翻译后结构完整
6. 失败显式：网关不可用 → 明确错误，**不返回原文**
7. 私密隔离：私密内容不进云端译文表

只有 LLM 网关走 `httpx.MockTransport`（传输层替身，拦 HTTP、不伪造业务数据，与
`backend/tests/test_llm_client.py` 同范式）—— 因为要**数 LLM 调用次数**，这正是
用例 1 和 4 的判据本身。数据库、Redis、判权、缓存全部是真的。

需要本地开发 PG（DATABASE_PORT=15432）与 Redis；不可达时跳过而非硬失败。
测试数据用 `t_i18nb_` 前缀，末尾清理，不污染库。
"""

from __future__ import annotations

import asyncio
import json
import uuid

from typing import Any

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_content_translations import HasnContentTranslations
from backend.app.hasn.service.content_translation_service import (
    RESOURCE_FIELDS,
    ContentTranslationService,
)
from backend.app.hasn_community.model.hasn_posts import HasnPosts
from backend.common.exception import errors
from backend.common.llm import LLMChatClient
from backend.common.translation import ContentTranslator
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_PREFIX = 't_i18nb'


# ============================ 夹具 ============================


@pytest_asyncio.fixture
async def pg_sessionmaker():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:  # 本地未起开发 PG → 跳过而非硬失败
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过内容翻译验收：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def cleanup(pg_sessionmaker):
    """测试后清掉本测试造的帖子与译文行。"""
    yield
    async with pg_sessionmaker() as session:
        await session.execute(
            sa.delete(HasnContentTranslations).where(
                HasnContentTranslations.resource_id.like(f'{_PREFIX}%')
            )
        )
        await session.execute(sa.delete(HasnPosts).where(HasnPosts.post_id.like(f'{_PREFIX}%')))
        await session.commit()


class _CountingGateway:
    """记数的 LLM 网关替身：统计真实发出的 chat completion 次数。"""

    def __init__(self, *, responder=None, status: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responder = responder
        self._status = status

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.calls.append(body)
            if self._status != 200:
                return httpx.Response(self._status, json={'error': 'gateway unavailable'})
            user_content = body['messages'][-1]['content']
            payload = self._responder(user_content) if self._responder else 'TRANSLATED'
            return httpx.Response(
                200,
                json={'choices': [{'message': {'content': payload}}], 'usage': {'total_tokens': 42}},
            )

        return httpx.MockTransport(handler)

    @property
    def call_count(self) -> int:
        return len(self.calls)


def _service(gateway: _CountingGateway) -> ContentTranslationService:
    """构造一个 LLM 走记数替身、其余（DB/Redis/判权/缓存）全真的 service 实例。"""
    service = ContentTranslationService()
    service._translator = ContentTranslator(
        LLMChatClient(
            base_url='http://gw.local',
            api_key='sk-test',
            model='agnes-2.5-flash',
            transport=gateway.transport(),
        )
    )
    return service


async def _make_post(
    pg_sessionmaker,
    *,
    content: str,
    author: str,
    visibility: str = 'public',
    status: str = 'published',
    circle_id: str | None = None,
) -> str:
    post_id = f'{_PREFIX}_{uuid.uuid4().hex[:12]}'
    async with pg_sessionmaker() as session:
        session.add(
            HasnPosts(
                post_id=post_id,
                author_type='human',
                author_hasn_id=author,
                author_user_id=None,
                owner_hasn_id=author,
                origin_workspace_kind='personal',
                origin_workspace_id='1',
                content=content,
                visibility=visibility,
                comment_policy='all',
                generation_type='human',
                status=status,
                circle_id=circle_id,
            )
        )
        await session.commit()
    return post_id


async def _set_post_content(pg_sessionmaker, post_id: str, content: str) -> None:
    async with pg_sessionmaker() as session:
        await session.execute(
            sa.update(HasnPosts).where(HasnPosts.post_id == post_id).values(content=content)
        )
        await session.commit()


# ============================ 1. 缓存命中 ============================


async def test_cache_hit_second_request_makes_no_new_llm_call(pg_sessionmaker, cleanup) -> None:
    """同资源同语言第二次请求 → cached=True，且 LLM 调用次数不增加。"""
    author = f'h_{uuid.uuid4()}'
    viewer = f'h_{uuid.uuid4()}'
    post_id = await _make_post(pg_sessionmaker, content='这是一条用于缓存验证的中文帖子。', author=author)

    gateway = _CountingGateway()
    service = _service(gateway)

    async with pg_sessionmaker() as db:
        first = await service.translate_resource(
            db, resource_kind='post', resource_id=post_id, fields=['content'],
            target_lang='en', viewer_hasn_id=viewer,
        )
    assert first['cached'] is False
    assert first['fields']['content'] == 'TRANSLATED'
    assert gateway.call_count == 1

    async with pg_sessionmaker() as db:
        second = await service.translate_resource(
            db, resource_kind='post', resource_id=post_id, fields=['content'],
            target_lang='en', viewer_hasn_id=viewer,
        )
    assert second['cached'] is True, '第二次必须命中缓存'
    assert second['fields']['content'] == 'TRANSLATED'
    assert gateway.call_count == 1, '命中缓存不得再调 LLM'

    # hit_count 应被记账（共享缓存摊薄效果的观测指标）
    async with pg_sessionmaker() as session:
        hit_count = (
            await session.execute(
                sa.select(HasnContentTranslations.hit_count).where(
                    HasnContentTranslations.resource_id == post_id
                )
            )
        ).scalar_one()
    assert hit_count >= 1


# ============================ 2. 原文变更失效 ============================


async def test_source_edit_invalidates_cache_and_retranslates(pg_sessionmaker, cleanup) -> None:
    """作者改了正文 → source_hash 变 → 重新翻译，绝不返回对不上原文的旧译文。"""
    author = f'h_{uuid.uuid4()}'
    viewer = f'h_{uuid.uuid4()}'
    post_id = await _make_post(pg_sessionmaker, content='第一版正文，中文内容。', author=author)

    versions = iter(['FIRST_VERSION', 'SECOND_VERSION'])
    gateway = _CountingGateway(responder=lambda _user: next(versions))
    service = _service(gateway)

    async with pg_sessionmaker() as db:
        first = await service.translate_resource(
            db, resource_kind='post', resource_id=post_id, fields=['content'],
            target_lang='en', viewer_hasn_id=viewer,
        )
    assert first['fields']['content'] == 'FIRST_VERSION'

    await _set_post_content(pg_sessionmaker, post_id, '第二版正文，内容已经改过了。')

    async with pg_sessionmaker() as db:
        second = await service.translate_resource(
            db, resource_kind='post', resource_id=post_id, fields=['content'],
            target_lang='en', viewer_hasn_id=viewer,
        )
    assert second['cached'] is False, '原文变了必须重译，不能命中旧缓存'
    assert second['fields']['content'] == 'SECOND_VERSION'
    assert second['fields']['content'] != 'FIRST_VERSION'
    assert gateway.call_count == 2

    # 两版译文各自成行（旧行保留可查历史，读路径只认当前 hash）
    async with pg_sessionmaker() as session:
        hashes = (
            await session.execute(
                sa.select(HasnContentTranslations.source_hash).where(
                    HasnContentTranslations.resource_id == post_id
                )
            )
        ).scalars().all()
    assert len(set(hashes)) == 2


# ============================ 3. 判权 ============================


async def test_private_post_is_forbidden_for_other_user(pg_sessionmaker, cleanup) -> None:
    """私密帖对他人 → 403，且一次 LLM 都不调。"""
    author = f'h_{uuid.uuid4()}'
    stranger = f'h_{uuid.uuid4()}'
    post_id = await _make_post(
        pg_sessionmaker, content='这是一条私密帖子。', author=author, visibility='private'
    )

    gateway = _CountingGateway()
    service = _service(gateway)

    async with pg_sessionmaker() as db:
        with pytest.raises(errors.ForbiddenError):
            await service.translate_resource(
                db, resource_kind='post', resource_id=post_id, fields=['content'],
                target_lang='en', viewer_hasn_id=stranger,
            )
    assert gateway.call_count == 0, '判权失败不得触发任何付费调用'


async def test_followers_only_post_forbidden_for_non_follower(pg_sessionmaker, cleanup) -> None:
    author = f'h_{uuid.uuid4()}'
    stranger = f'h_{uuid.uuid4()}'
    post_id = await _make_post(
        pg_sessionmaker, content='仅关注者可见的内容。', author=author, visibility='followers'
    )
    service = _service(_CountingGateway())
    async with pg_sessionmaker() as db:
        with pytest.raises(errors.ForbiddenError):
            await service.translate_resource(
                db, resource_kind='post', resource_id=post_id, fields=['content'],
                target_lang='en', viewer_hasn_id=stranger,
            )


async def test_draft_post_is_not_translatable_even_for_author(pg_sessionmaker, cleanup) -> None:
    """草稿不给翻——连作者自己也不行（草稿反复编辑会让缓存每次失效，等于按编辑次数烧钱）。"""
    author = f'h_{uuid.uuid4()}'
    post_id = await _make_post(
        pg_sessionmaker, content='草稿内容。', author=author, status='draft'
    )
    service = _service(_CountingGateway())
    async with pg_sessionmaker() as db:
        with pytest.raises(errors.NotFoundError):
            await service.translate_resource(
                db, resource_kind='post', resource_id=post_id, fields=['content'],
                target_lang='en', viewer_hasn_id=author,
            )


async def test_author_can_translate_own_private_post(pg_sessionmaker, cleanup) -> None:
    """作者本人可以翻自己的已发布私密帖（判权是「看得到才准翻」，不是一刀切）。"""
    author = f'h_{uuid.uuid4()}'
    post_id = await _make_post(
        pg_sessionmaker, content='我自己的私密帖内容。', author=author, visibility='private'
    )
    gateway = _CountingGateway()
    service = _service(gateway)
    async with pg_sessionmaker() as db:
        result = await service.translate_resource(
            db, resource_kind='post', resource_id=post_id, fields=['content'],
            target_lang='en', viewer_hasn_id=author,
        )
    assert result['fields']['content'] == 'TRANSLATED'


# ============================ 4. 并发首译 ============================


async def test_concurrent_first_translation_calls_llm_once(pg_sessionmaker, cleanup) -> None:
    """10 个并发请求同一条帖子 → Redis 短锁收敛，只产生 1 次 LLM 调用。"""
    author = f'h_{uuid.uuid4()}'
    viewer = f'h_{uuid.uuid4()}'
    post_id = await _make_post(
        pg_sessionmaker, content='一条会被十个人同时点翻译的热帖内容。', author=author
    )

    async def slow_responder(_user: str) -> str:
        return 'TRANSLATED'

    gateway = _CountingGateway(responder=lambda user: slow_responder and 'TRANSLATED')
    service = _service(gateway)

    async def one_request() -> dict[str, Any]:
        # 每个并发请求各自一个 DB 会话（AsyncSession 不是并发安全的）
        async with pg_sessionmaker() as db:
            return await service.translate_resource(
                db, resource_kind='post', resource_id=post_id, fields=['content'],
                target_lang='en', viewer_hasn_id=viewer,
            )

    results = await asyncio.gather(*(one_request() for _ in range(10)))

    assert all(item['fields']['content'] == 'TRANSLATED' for item in results)
    assert gateway.call_count == 1, (
        f'并发首译应只调一次 LLM，实际 {gateway.call_count} 次——热帖会按并发人数烧钱'
    )

    # 缓存里对这条 (resource, field, lang, hash) 只应有一行
    async with pg_sessionmaker() as session:
        rows = (
            await session.execute(
                sa.select(sa.func.count()).select_from(HasnContentTranslations).where(
                    HasnContentTranslations.resource_id == post_id
                )
            )
        ).scalar_one()
    assert rows == 1


# ============================ 5. 结构保留 ============================


async def test_structure_preserved_for_code_url_mention_and_hasn_uri(
    pg_sessionmaker, cleanup
) -> None:
    """含代码块/URL/@提及/#话题/hasn:// URI 的帖子，翻完这些片段必须原样还在。"""
    author = f'h_{uuid.uuid4()}'
    viewer = f'h_{uuid.uuid4()}'
    content = (
        '@张三 你看看 #唤星# 这段 `render()`：\n\n'
        '```python\nprint("hello world")\n```\n\n'
        '文档在 https://docs.example.com/guide 详情见 hasn://community/posts/p_abc'
    )
    post_id = await _make_post(pg_sessionmaker, content=content, author=author)

    def responder(user_content: str) -> str:
        # 模型只翻散文、原样保留占位符（这是我们 prompt 要求的正常行为）
        masked = user_content.split('\n\n', 1)[1]
        return masked.replace('你看看', 'take a look at').replace('文档在', 'docs at').replace(
            '详情见', 'details at'
        )

    gateway = _CountingGateway(responder=responder)
    service = _service(gateway)

    async with pg_sessionmaker() as db:
        # 目标语言取 ja：这条帖子混了大量代码/URL/英文，语言检测可能判成 en，
        # 若目标也是 en 就会走「同语言跳过」而根本不调 LLM，结构断言就成了空转。
        result = await service.translate_resource(
            db, resource_kind='post', resource_id=post_id, fields=['content'],
            target_lang='ja', viewer_hasn_id=viewer,
        )

    assert result['skipped'] is False, '本用例必须真的走翻译，否则结构断言在验原文'
    assert gateway.call_count == 1
    translated = result['fields']['content']
    for fragment in (
        '@张三',
        '#唤星#',
        '`render()`',
        'print("hello world")',
        '```python',
        'https://docs.example.com/guide',
        'hasn://community/posts/p_abc',
    ):
        assert fragment in translated, f'结构片段 {fragment!r} 在译文中丢失'
    assert 'take a look at' in translated, '散文部分应当被翻译'


async def test_structure_damage_fails_instead_of_returning_broken_text(
    pg_sessionmaker, cleanup
) -> None:
    """模型把占位符吞了 → 显式失败，不交付一段链接和代码块被翻烂的正文。"""
    author = f'h_{uuid.uuid4()}'
    viewer = f'h_{uuid.uuid4()}'
    post_id = await _make_post(
        pg_sessionmaker, content='看这段代码 `foo()` 和 https://a.example/x', author=author
    )
    gateway = _CountingGateway(responder=lambda _user: 'look at this code, link removed')
    service = _service(gateway)

    async with pg_sessionmaker() as db:
        # 同上：目标语言取 ja，确保真的走翻译而不是被「同语言跳过」短路。
        with pytest.raises(errors.GatewayError):
            await service.translate_resource(
                db, resource_kind='post', resource_id=post_id, fields=['content'],
                target_lang='ja', viewer_hasn_id=viewer,
            )
    assert gateway.call_count == 1, '必须真的调过 LLM，否则这条断言在验空转'

    async with pg_sessionmaker() as session:
        rows = (
            await session.execute(
                sa.select(sa.func.count()).select_from(HasnContentTranslations).where(
                    HasnContentTranslations.resource_id == post_id
                )
            )
        ).scalar_one()
    assert rows == 0, '结构校验没过的译文不得进缓存'


# ============================ 6. 失败显式 ============================


async def test_gateway_down_fails_explicitly_and_never_returns_source_text(
    pg_sessionmaker, cleanup
) -> None:
    """网关不可用 → 抛 GatewayError（502）。**绝不返回原文伪装成译文。**"""
    author = f'h_{uuid.uuid4()}'
    viewer = f'h_{uuid.uuid4()}'
    original = '这段中文原文绝不能被当成译文返回给用户。'
    post_id = await _make_post(pg_sessionmaker, content=original, author=author)

    gateway = _CountingGateway(status=503)
    service = _service(gateway)

    async with pg_sessionmaker() as db:
        with pytest.raises(errors.GatewayError) as caught:
            await service.translate_resource(
                db, resource_kind='post', resource_id=post_id, fields=['content'],
                target_lang='en', viewer_hasn_id=viewer,
            )
    assert original not in str(getattr(caught.value, 'msg', '')), '错误信息里也不该把原文当译文回吐'

    async with pg_sessionmaker() as session:
        rows = (
            await session.execute(
                sa.select(sa.func.count()).select_from(HasnContentTranslations).where(
                    HasnContentTranslations.resource_id == post_id
                )
            )
        ).scalar_one()
    assert rows == 0, '失败不得写缓存'


async def test_same_language_is_skipped_not_translated(pg_sessionmaker, cleanup) -> None:
    """源语言==目标语言 → skipped=True + 原文，不调 LLM。

    这与「翻译失败回落原文」是**完全不同的语义**：这里明确告诉客户端「没翻，因为不用翻」。
    """
    author = f'h_{uuid.uuid4()}'
    viewer = f'h_{uuid.uuid4()}'
    original = '这是一段完整的中文帖子内容，语言检测应当判为中文。'
    post_id = await _make_post(pg_sessionmaker, content=original, author=author)

    gateway = _CountingGateway()
    service = _service(gateway)
    async with pg_sessionmaker() as db:
        result = await service.translate_resource(
            db, resource_kind='post', resource_id=post_id, fields=['content'],
            target_lang='zh', viewer_hasn_id=viewer,
        )
    assert result['skipped'] is True
    assert result['fields']['content'] == original
    assert gateway.call_count == 0


# ============================ 7. 私密隔离 ============================


async def test_private_domain_kinds_are_not_translatable_on_cloud(pg_sessionmaker, cleanup) -> None:
    """云端译文表只承载**公共**内容。

    私聊/群聊/分身会话属私密域，走本机 daemon 本地链路（P5），云端**没有**对应的
    resource_kind：既没法调用，也就不可能有任何一行私密译文落进 `hasn_content_translations`。
    这是本地优先铁律在云端侧的可执行守卫。
    """
    assert set(RESOURCE_FIELDS) == {'post', 'article', 'comment', 'circle', 'profile'}
    for private_kind in ('message', 'im', 'conversation', 'agent_session', 'document', 'memory'):
        assert private_kind not in RESOURCE_FIELDS, f'私密域 {private_kind} 不得出现在云端翻译资源表'

    service = _service(_CountingGateway())
    async with pg_sessionmaker() as db:
        with pytest.raises(errors.RequestError):
            await service.translate_resource(
                db, resource_kind='message', resource_id='m_whatever', fields=['content'],
                target_lang='en', viewer_hasn_id=f'h_{uuid.uuid4()}',
            )


async def test_cloud_translation_table_holds_no_private_resource_kinds(pg_sessionmaker) -> None:
    """DB 查证：云端译文表里实际存在的 resource_kind 全部属于公共域白名单。"""
    async with pg_sessionmaker() as session:
        kinds = (
            await session.execute(sa.select(HasnContentTranslations.resource_kind).distinct())
        ).scalars().all()
    unexpected = [kind for kind in kinds if kind not in RESOURCE_FIELDS]
    assert not unexpected, f'云端译文表出现非公共域 resource_kind: {unexpected}'


# ============================ 入参守卫 ============================


async def test_field_whitelist_rejects_arbitrary_columns(pg_sessionmaker, cleanup) -> None:
    """field 走白名单：不许拿它把任意列读出来当翻译输入。"""
    author = f'h_{uuid.uuid4()}'
    post_id = await _make_post(pg_sessionmaker, content='内容。', author=author)
    service = _service(_CountingGateway())
    async with pg_sessionmaker() as db:
        with pytest.raises(errors.RequestError):
            await service.translate_resource(
                db, resource_kind='post', resource_id=post_id, fields=['owner_hasn_id'],
                target_lang='en', viewer_hasn_id=author,
            )


async def test_oversized_text_is_rejected(pg_sessionmaker, cleanup, monkeypatch) -> None:
    """超长文本直接拒绝，不截断——截断会产出半截译文。"""
    from backend.core.conf import settings

    author = f'h_{uuid.uuid4()}'
    post_id = await _make_post(pg_sessionmaker, content='内' * 500, author=author)
    monkeypatch.setattr(settings, 'CONTENT_TRANSLATION_MAX_CHARS', 100)

    gateway = _CountingGateway()
    service = _service(gateway)
    async with pg_sessionmaker() as db:
        with pytest.raises(errors.RequestError):
            await service.translate_resource(
                db, resource_kind='post', resource_id=post_id, fields=['content'],
                target_lang='en', viewer_hasn_id=author,
            )
    assert gateway.call_count == 0
