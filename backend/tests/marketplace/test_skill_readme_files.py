"""技能详情正文（readme）+ 文件清单 真实测试（doc13）。

四层覆盖，全部零业务 mock：
  1) 解析（真实文件）：_extract_skill_body 去 frontmatter 留正文；_list_skill_files
     递归列出名称+大小、过滤 隐藏/.pyc/__pycache__、且**只含 path+size 不含内容**。
  2) 序列化（真实 PG）：插入带 body_en/body_zh/files 的技能行 → get_skill_detail 按语言
     取 readme（缺失回退另一语言）+ files 解析 + file_count，detail 不泄露文件内容。
  3) 翻译门控（真实 translate_markdown，仅打桩 LLM 网络出口 _complete_chat）：源语言侧存原文、
     另一侧存翻译。
  4) 复用门控：已存在同源正文 + 已有译文 → 复用、不再触网；空正文 → 两侧清空。

序列化测试需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import json
import uuid

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.service.github_sync_service import GitHubSyncService, github_sync_service
from backend.app.marketplace.service.search_service import search_service
from backend.app.marketplace.service.translation_service import translation_service
from backend.database.db import SQLALCHEMY_DATABASE_URL


# --------------------------------------------------------------------------- #
# 1) 解析层：纯函数 + 真实文件，无 DB / 无 LLM
# --------------------------------------------------------------------------- #

SKILL_MD = """---
name: Demo Skill
description: A demo
version: 1.2.0
---

# Demo Skill

Use this when you need a demo.

## Steps
1. First
2. Second
"""


def test_extract_skill_body_strips_frontmatter():
    body = GitHubSyncService._extract_skill_body(SKILL_MD)
    assert body.startswith('# Demo Skill'), body
    assert 'name: Demo Skill' not in body  # frontmatter 不在正文
    assert 'version: 1.2.0' not in body
    assert '## Steps' in body


def test_extract_skill_body_without_frontmatter_passthrough():
    raw = '# Just a heading\n\nNo frontmatter here.'
    assert GitHubSyncService._extract_skill_body(raw) == raw.strip()


def test_list_skill_files_names_sizes_only_no_content(tmp_path: Path):
    # 构造一个真实技能目录：根 SKILL.md + 子目录脚本/引用 + 应被过滤的项。
    (tmp_path / 'SKILL.md').write_text(SKILL_MD, encoding='utf-8')
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts' / 'run.py').write_text('print("hi")\n', encoding='utf-8')
    (tmp_path / 'references').mkdir()
    (tmp_path / 'references' / 'guide.md').write_text('# Guide\n', encoding='utf-8')
    # 应被过滤：隐藏文件、.pyc、__pycache__、隐藏目录
    (tmp_path / '.hidden').write_text('secret', encoding='utf-8')
    (tmp_path / 'scripts' / 'run.pyc').write_text('x', encoding='utf-8')
    (tmp_path / '__pycache__').mkdir()
    (tmp_path / '__pycache__' / 'cache.bin').write_text('x', encoding='utf-8')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git' / 'config').write_text('x', encoding='utf-8')

    files = GitHubSyncService._list_skill_files(tmp_path)
    paths = [f['path'] for f in files]

    # 递归相对路径（POSIX），按字典序稳定排序
    assert paths == ['SKILL.md', 'references/guide.md', 'scripts/run.py'], paths
    # 隐藏/.pyc/__pycache__/.git 全部被排除
    assert not any('.hidden' in p or '.pyc' in p or '__pycache__' in p or '.git' in p for p in paths)
    # 每项只含 path + size，绝不含文件内容
    for f in files:
        assert set(f.keys()) == {'path', 'size'}, f
        assert isinstance(f['size'], int) and f['size'] >= 0


# --------------------------------------------------------------------------- #
# 2) 序列化层：真实 PG，无 LLM
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


def _files_json() -> str:
    return json.dumps(
        [
            {'path': 'SKILL.md', 'size': 320},
            {'path': 'references/guide.md', 'size': 64},
            {'path': 'scripts/run.py', 'size': 12},
        ],
        ensure_ascii=False,
    )


async def _seed_skill(session, skill_id, namespace, slug, **cols):
    await session.execute(delete(MarketplaceSkill).where(MarketplaceSkill.skill_id == skill_id))
    session.add(
        MarketplaceSkill(
            skill_id=skill_id, namespace=namespace, slug=slug,
            name=cols.pop('name', 'Demo'), status='published', visibility='public', **cols,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_detail_emits_readme_per_lang_files_and_count(db_session):
    tag = uuid.uuid4().hex[:8]
    namespace, slug = 'huanxing/demo', f'readme-{tag}'
    skill_id = f'{namespace}/{slug}'
    await _seed_skill(
        db_session, skill_id, namespace, slug,
        body_en='# Demo\n\nEnglish body.',
        body_zh='# 演示\n\n中文正文。',
        files=_files_json(),
    )

    detail_zh = await search_service.get_skill_detail(db_session, skill_id, 'zh')
    assert detail_zh is not None
    assert detail_zh['readme'] == '# 演示\n\n中文正文。'       # zh → 中文正文
    assert detail_zh['readme_en'] == '# Demo\n\nEnglish body.'
    assert detail_zh['readme_zh'] == '# 演示\n\n中文正文。'
    # files 解析为 [{path,size}]，count 对齐，且**不含任何文件内容字段**
    assert detail_zh['file_count'] == 3
    assert [f['path'] for f in detail_zh['files']] == ['SKILL.md', 'references/guide.md', 'scripts/run.py']
    for f in detail_zh['files']:
        assert set(f.keys()) == {'path', 'size'}, f

    detail_en = await search_service.get_skill_detail(db_session, skill_id, 'en')
    assert detail_en['readme'] == '# Demo\n\nEnglish body.'    # en → 英文正文


@pytest.mark.asyncio
async def test_detail_readme_falls_back_to_other_language(db_session):
    tag = uuid.uuid4().hex[:8]
    namespace, slug = 'huanxing/demo', f'fallback-{tag}'
    skill_id = f'{namespace}/{slug}'
    # 只有英文正文：请求 zh 时回退英文，不返回空。
    await _seed_skill(
        db_session, skill_id, namespace, slug,
        body_en='# Only EN\n\nbody', body_zh=None, files=None,
    )
    detail_zh = await search_service.get_skill_detail(db_session, skill_id, 'zh')
    assert detail_zh['readme'] == '# Only EN\n\nbody'          # 回退英文
    assert detail_zh['files'] == [] and detail_zh['file_count'] == 0  # 无文件清单 → 空


# --------------------------------------------------------------------------- #
# 3) + 4) 翻译/复用门控：真实 translate_markdown，仅打桩 LLM 网络出口
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_resolve_bilingual_body_translates_new_skill(monkeypatch):
    translation_service._translation_cache.clear()

    async def _fake_complete_chat(messages, **kwargs):
        # 断言确实走的是 markdown 翻译提示（保留结构），返回确定性译文
        assert any('Markdown' in m.get('content', '') for m in messages)
        return '# 演示\n\n已翻译正文。'

    monkeypatch.setattr(translation_service, '_complete_chat', _fake_complete_chat)

    body_en, body_zh = await github_sync_service._resolve_bilingual_body(
        existing_skill=None, source_language='en', body='# Demo\n\nEnglish body.',
    )
    assert body_en == '# Demo\n\nEnglish body.'   # 原文存源语言侧
    assert body_zh == '# 演示\n\n已翻译正文。'      # 译文存另一侧


@pytest.mark.asyncio
async def test_resolve_bilingual_body_reuses_cached_translation(monkeypatch):
    translation_service._translation_cache.clear()
    body = '# Demo\n\nUnchanged body.'

    async def _must_not_touch_network(*_a, **_k):
        raise AssertionError('未变更正文不应再次翻译（触网）')

    monkeypatch.setattr(translation_service, '_complete_chat', _must_not_touch_network)

    existing = SimpleNamespace(body_en=body, body_zh='# 演示\n\n旧译文。')
    body_en, body_zh = await github_sync_service._resolve_bilingual_body(
        existing_skill=existing, source_language='en', body=body,
    )
    assert body_en == body
    assert body_zh == '# 演示\n\n旧译文。'           # 复用已有译文，未触网


@pytest.mark.asyncio
async def test_resolve_bilingual_body_detects_body_language_not_name_hint(monkeypatch):
    """正文语言按正文本身判定，而非沿用 name 推出的 source_language。

    活体回填发现：英文名/中文正文（source_language='en' 但正文是中文）若按名字判，
    中文正文会落到英文侧、两侧同为中文。修复后按正文判，src=zh。
    """
    translation_service._translation_cache.clear()
    chinese_body = '# 代码审查清单\n\n系统、全面的代码审查方法。逐维度有序检查，而非随机扫描。'

    async def _fake_complete_chat(messages, **kwargs):
        return '# Code Review Checklist\n\nA systematic, comprehensive approach.'

    monkeypatch.setattr(translation_service, '_complete_chat', _fake_complete_chat)

    body_en, body_zh = await github_sync_service._resolve_bilingual_body(
        existing_skill=None, source_language='en', body=chinese_body,
    )
    assert body_zh == chinese_body                       # 中文正文落中文侧（不被英文名误导）
    assert body_en == '# Code Review Checklist\n\nA systematic, comprehensive approach.'  # 英文侧是译文


@pytest.mark.asyncio
async def test_resolve_bilingual_body_empty_clears_both_sides():
    body_en, body_zh = await github_sync_service._resolve_bilingual_body(
        existing_skill=SimpleNamespace(body_en='old', body_zh='旧'),
        source_language='en', body='   ',
    )
    assert body_en is None and body_zh is None      # 空正文 → 两侧清空（诚实：无 readme）
