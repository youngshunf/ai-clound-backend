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
from backend.app.marketplace.service.skill_content_extractor import detect_body_source_lang
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


@pytest.mark.asyncio
async def test_detail_resolves_bare_slug_not_only_namespaced_id(db_session):
    """技能包/模板成员按裸 slug 命名存储（hermes 成员命名），详情查询必须能用裸 slug 解析，
    否则真实在库的市场技能会被错判「未在市场找到」（university-applications 404 事故）。"""
    tag = uuid.uuid4().hex[:8]
    namespace, slug = 'clawhub/wscats', f'uniapp-{tag}'
    skill_id = f'{namespace}/{slug}'
    await _seed_skill(
        db_session, skill_id, namespace, slug,
        name='University Applications', body_en='# Apps\n\nbody', files=None,
    )

    # 完整 namespace/slug 仍解析
    by_full = await search_service.get_skill_detail(db_session, skill_id, 'zh')
    assert by_full is not None and by_full['name'] == 'University Applications'

    # 裸 slug（无 namespace）也解析到同一行
    by_slug = await search_service.get_skill_detail(db_session, slug, 'zh')
    assert by_slug is not None
    assert by_slug['skill_id'] == skill_id
    assert by_slug['name'] == 'University Applications'

    # 不存在的裸 slug 仍诚实返回 None（→ 路由 404 → 前端优雅降级，不伪造）
    missing = await search_service.get_skill_detail(db_session, f'no-such-{tag}', 'zh')
    assert missing is None


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


@pytest.mark.asyncio
async def test_resolve_bilingual_body_echo_guard_drops_untranslated(monkeypatch):
    """零 fake 回声闸门：超长正文可能超模型预算被原样回吐（译文==原文），这不是翻译。

    活体回填发现：一篇 14k+ 中文正文翻译后 body_en 与 body_zh 等长——网关原样回吐。
    若不拦，会把中文原文当英文译文落库（且变更门控会永久复用该假译文）。
    修复：译文与原文逐字相同 → 视为未翻译，目标侧置空、序列化时回退源语言正文。
    """
    translation_service._translation_cache.clear()
    chinese_body = '# 代码审查清单\n\n系统、全面的代码审查方法。逐维度有序检查。'

    async def _echo(messages, **kwargs):
        return chinese_body  # 网关原样回吐源文（退化/超预算）

    monkeypatch.setattr(translation_service, '_complete_chat', _echo)

    body_en, body_zh = await github_sync_service._resolve_bilingual_body(
        existing_skill=None, source_language='zh', body=chinese_body,
    )
    assert body_zh == chinese_body   # 中文正文落中文侧
    assert body_en is None           # 回声译文被丢弃，不把中文当英文译文落库


# --------------------------------------------------------------------------- #
# 5) clawhub 同源捕获：从解压目录读取正文+文件清单（与 github 共享提取逻辑）
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_clawhub_extract_body_and_files_from_disk(tmp_path: Path, monkeypatch):
    """clawhub 同步从解压后的技能目录提取正文(双语)+文件清单，与 github 同源。"""
    from backend.app.marketplace.service.clawhub_sync_service import clawhub_sync_service

    (tmp_path / 'SKILL.md').write_text(SKILL_MD, encoding='utf-8')  # 正文为英文
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts' / 'run.py').write_text('print("hi")\n', encoding='utf-8')
    (tmp_path / '__pycache__').mkdir()
    (tmp_path / '__pycache__' / 'c.bin').write_text('x', encoding='utf-8')  # 应被过滤

    translation_service._translation_cache.clear()

    async def _fake(messages, **kwargs):
        return '# 演示\n\n已翻译正文。'

    monkeypatch.setattr(translation_service, '_complete_chat', _fake)

    body_en, body_zh, files_json = await clawhub_sync_service._extract_body_and_files(
        existing_skill=None, source_language='en', skill_dir=tmp_path,
    )
    assert body_en.startswith('# Demo Skill')   # 英文正文落英文侧
    assert body_zh == '# 演示\n\n已翻译正文。'    # 中文侧是译文
    files = json.loads(files_json)
    assert [f['path'] for f in files] == ['SKILL.md', 'scripts/run.py']   # __pycache__ 被过滤
    for f in files:
        assert set(f.keys()) == {'path', 'size'}, f   # 仅名称+大小，不含内容


def test_split_markdown_for_translation_chunks_without_breaking_fences():
    """长文档按段落分块（≤budget），且**绝不**切开代码围栏；拼回恢复原文。

    本地翻译网关对超长 Markdown 会原样回吐，分块后逐块翻译可避开。围栏内含空行（多段）
    时仍须整块留在同一 chunk（``` 计数为偶才允许切块）。
    """
    split = translation_service._split_markdown_for_translation

    # 小文档 → 单块（行为不变）
    assert split('# Hi\n\nshort body') == ['# Hi\n\nshort body']

    # 跨多段落的代码围栏 + 前后散文 → 多块，每块 ``` 平衡，拼回等于原文
    code = '```python\n' + '\n\n'.join(f'x{i} = {i}' for i in range(60)) + '\n```'
    doc = 'intro paragraph one.\n\n' + ('prose ' * 80) + '\n\n' + code + '\n\n' + ('tail ' * 80)
    chunks = split(doc, budget=500)
    assert len(chunks) > 1
    for c in chunks:
        assert c.count('```') % 2 == 0, c[:60]   # 没有块停在未闭合围栏中
    assert '\n\n'.join(chunks) == doc            # 无损拼回


def test_detect_body_source_lang_cjk_ratio_beats_langdetect():
    """中英混排技术文档：CJK 占比为主信号，盖过 langdetect 的误判。

    活体回填发现：tencent-mps/university-applications 等中文 SKILL.md（含大量英文 API 名/
    代码）CJK 占比 25%~29%，langdetect 却判成 'en'，导致中文正文落英文侧。改用 CJK 占比
    （≥10% 即中文源）后稳定判 'zh'；纯英文文档仍判 'en'。
    """
    # 中文正文 + 大量英文 API 名/代码（约 1/4 CJK）→ 必须判 zh（即便 langdetect 说 en）
    mixed = (
        '# 腾讯云媒体处理服务（MPS）\n\n## 角色定义\n你是腾讯云 MPS 助手。\n\n'
        '调用 DescribeTaskDetail / CreateTranscodeTemplate 接口，'
        'set region=ap-guangzhou, bucket=my-bucket, callback=https://example.com/cb\n'
        '示例：对视频做转码、截图、审核，输出到 COS 存储桶。'
    )
    assert sum(1 for c in mixed if '一' <= c <= '鿿') / len(mixed) >= 0.10
    assert detect_body_source_lang(mixed, source_language='en') == 'zh'   # 不被 en 名/langdetect 误导

    pure_en = '# Code Review\n\nA systematic approach to reviewing pull requests across stacks.'
    assert detect_body_source_lang(pure_en, source_language='zh') == 'en'  # 纯英文仍判 en


@pytest.mark.asyncio
async def test_clawhub_extract_missing_skill_md_is_honest_empty(tmp_path: Path):
    """目录无 SKILL.md → 正文两侧空、文件清单为 []（零 fake，如实反映无正文）。"""
    from backend.app.marketplace.service.clawhub_sync_service import clawhub_sync_service

    (tmp_path / 'desc.txt').write_text('只有附带文件，没有 SKILL.md', encoding='utf-8')
    body_en, body_zh, files_json = await clawhub_sync_service._extract_body_and_files(
        existing_skill=None, source_language='en', skill_dir=tmp_path,
    )
    assert body_en is None and body_zh is None
    assert [f['path'] for f in json.loads(files_json)] == ['desc.txt']   # 文件清单仍如实列出
