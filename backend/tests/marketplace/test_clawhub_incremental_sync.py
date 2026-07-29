"""ClawHub 元数据同步：版本级跳过、元数据变更门控与源侧原文测试。

锁住本次「周期同步不再每轮全量重下载 + 全量重译」改造的契约：

- ``_is_version_unchanged``：上游 latestVersion 与库内最新版本一致，且目录元数据未变
  → 整条跳过（只刷计数）；不再依赖服务器正文和 ``repo_path``。
- ``_bilingual_metadata``：name/description 源语言侧存原文逐字（让下次元数据门控能命中）。
- ``_batch_prepare_metadata``：变更门控真实分流——只把改动 / 新增 / force 的技能送 LLM，
  未变技能复用库内缓存译文（用 recorder 断言零调用）。
"""
from __future__ import annotations

import asyncio

from types import SimpleNamespace
from typing import Any

from backend.app.marketplace.service import clawhub_sync_service as mod
from backend.app.marketplace.service.clawhub_sync_service import clawhub_sync_service

# ---------- 测试夹具 ----------


def _existing(**kw) -> Any:
    """库内现有 clawhub 行替身。源语言 zh → 源侧（description_zh）存 summary 原文逐字。"""
    base = {
        'id': 1,
        'skill_id': 'clawhub/alice/translator',
        'slug': 'translator',
        'namespace': 'clawhub/alice',
        'name': '翻译大师',          # 顶层 name = displayName（原文）
        'name_en': 'Translator Pro',
        'name_zh': '翻译大师',
        'description_en': 'A pro translator',
        'description_zh': '一个专业的翻译工具',  # 源侧 = summary 原文逐字
        'files': '[{"path":"SKILL.md","size":120,"sha256":"' + ('a' * 64) + '"}]',
        'source_language': 'zh',
        'tags_en': '["translate"]',
        'tags_zh': '["翻译"]',
        'emoji': '🌐',
        'category': 'productivity',
        'author_name': 'alice',
        'repo_path': 'clawhub/alice/translator',
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _skill(**kw) -> dict:
    """ClawHub 列表项替身（displayName/summary/slug/stats/latestVersion）。"""
    base = {
        'slug': 'translator',
        'displayName': '翻译大师',
        'summary': '一个专业的翻译工具',
        'tags': ['翻译'],
        'stats': {'downloads': 500, 'stars': 9},
        'latestVersion': {'version': '1.0.0'},
    }
    base.update(kw)
    return base


# ---------- _is_version_unchanged ----------

def test_version_unchanged_true_when_version_matches_and_body_and_repo() -> None:
    existing = _existing()
    skill = _skill(latestVersion={'version': '1.0.0'})
    assert clawhub_sync_service._is_version_unchanged(
        existing, skill, {'clawhub/alice/translator': '1.0.0'}, {'clawhub/alice/translator'}
    ) is True


def test_version_unchanged_false_when_upstream_version_differs() -> None:
    existing = _existing()
    skill = _skill(latestVersion={'version': '2.0.0'})  # 上游升版
    assert clawhub_sync_service._is_version_unchanged(
        existing, skill, {'clawhub/alice/translator': '1.0.0'}, {'clawhub/alice/translator'}
    ) is False


def test_version_unchanged_does_not_require_server_body() -> None:
    existing = _existing()
    skill = _skill()
    assert clawhub_sync_service._is_version_unchanged(
        existing, skill, {'clawhub/alice/translator': '1.0.0'}, set()
    ) is True


def test_version_unchanged_does_not_require_repo_path() -> None:
    existing = _existing(repo_path=None)
    skill = _skill()
    assert clawhub_sync_service._is_version_unchanged(
        existing, skill, {'clawhub/alice/translator': '1.0.0'}, {'clawhub/alice/translator'}
    ) is True


def test_version_unchanged_requires_verified_file_manifest() -> None:
    existing = _existing(files='[{"path":"SKILL.md","size":120}]')
    skill = _skill()
    assert clawhub_sync_service._is_version_unchanged(
        existing, skill, {'clawhub/alice/translator': '1.0.0'}
    ) is False


def test_version_unchanged_false_when_no_upstream_version() -> None:
    existing = _existing()
    skill = _skill(latestVersion={})
    assert clawhub_sync_service._is_version_unchanged(
        existing, skill, {'clawhub/alice/translator': '1.0.0'}, {'clawhub/alice/translator'}
    ) is False


# ---------- _bilingual_metadata（源侧 verbatim）----------

def test_bilingual_metadata_zh_source_keeps_verbatim_on_zh_side() -> None:
    translated = {
        'name_en': 'Translator Pro', 'name_zh': 'LLM重排过的名字',
        'description_en': 'A pro translator', 'description_zh': 'LLM重排过的描述',
        'source_language': 'zh',
    }
    name_en, name_zh, desc_en, desc_zh = clawhub_sync_service._bilingual_metadata(
        translated, name='翻译大师', description='一个专业的翻译工具',
    )
    # 源语言侧（zh）= 原文逐字；另一侧（en）= LLM 译文
    assert name_zh == '翻译大师'
    assert desc_zh == '一个专业的翻译工具'
    assert name_en == 'Translator Pro'
    assert desc_en == 'A pro translator'


def test_bilingual_metadata_en_source_keeps_verbatim_on_en_side() -> None:
    translated = {
        'name_en': 'rewritten', 'name_zh': '助手',
        'description_en': 'rewritten desc', 'description_zh': '助手描述',
        'source_language': 'en',
    }
    name_en, name_zh, desc_en, desc_zh = clawhub_sync_service._bilingual_metadata(
        translated, name='Assistant', description='An assistant',
    )
    assert name_en == 'Assistant'
    assert desc_en == 'An assistant'
    assert name_zh == '助手'
    assert desc_zh == '助手描述'


# ---------- _batch_prepare_metadata（变更门控）----------

def _make_recorder():
    sent: list[list[dict]] = []

    async def _recorder(items, *, batch_size=None, concurrency=3):
        sent.append(items)
        return [
            {
                'name_en': it['name'], 'name_zh': it['name'],
                'description_en': it['description'], 'description_zh': it['description'],
                'tags_en': [], 'tags_zh': [], 'emoji': None,
                'category': 'other', 'source_language': it.get('source_lang'),
            }
            for it in items
        ]

    return _recorder, sent


def test_batch_prepare_only_sends_changed_or_new_to_llm(monkeypatch) -> None:
    # 库内已有 translator（未变）；helper（描述变了）；新增 newbie（库内无）。
    existing_by_slug = {
        'translator': _existing(),
        'helper': _existing(
            skill_id='clawhub/bob/helper', slug='helper', namespace='clawhub/bob',
            name='助手', name_en='Helper', name_zh='助手',
            description_en='old', description_zh='旧描述', author_name='bob',
            repo_path='clawhub/bob/helper',
        ),
    }
    skills = [
        _skill(),  # translator，未变 → 复用缓存
        _skill(slug='helper', displayName='助手', summary='新描述（变了）',
               latestVersion={'version': '1.0.0'}),
        _skill(slug='newbie', displayName='New Skill', summary='Brand new',
               latestVersion={'version': '1.0.0'}),
    ]

    recorder, sent = _make_recorder()
    monkeypatch.setattr(mod.translation_service, 'batch_translate_skill_metadata', recorder)

    prepared = asyncio.run(
        clawhub_sync_service._batch_prepare_metadata(skills, existing_by_slug)
    )

    # 只 helper、newbie 被送 LLM（translator 未变复用缓存）。
    assert len(sent) == 1
    sent_names = {it['name'] for it in sent[0]}
    assert sent_names == {'助手', 'New Skill'}
    # translator 复用库内缓存译文（零 LLM）。
    assert prepared['translator']['name_en'] == 'Translator Pro'
    assert prepared['translator']['tags_zh'] == ['翻译']  # translation_from_existing 解析 JSON
    # helper / newbie 走 LLM 拿到新值。
    assert prepared['helper']['description_zh'] == '新描述（变了）'
    assert prepared['newbie']['name_en'] == 'New Skill'


def test_batch_prepare_all_cached_zero_llm(monkeypatch) -> None:
    """全部未变 → 一次 LLM 都不调（核心省钱点）。"""
    existing_by_slug = {'translator': _existing()}
    skills = [_skill()]

    called = {'n': 0}

    async def _recorder(items, *, batch_size=None, concurrency=3):
        called['n'] += 1
        return []

    monkeypatch.setattr(mod.translation_service, 'batch_translate_skill_metadata', _recorder)
    prepared = asyncio.run(
        clawhub_sync_service._batch_prepare_metadata(skills, existing_by_slug)
    )

    assert called['n'] == 0
    assert prepared['translator']['name_en'] == 'Translator Pro'


def test_batch_prepare_force_sends_all_even_unchanged(monkeypatch) -> None:
    """force=True 全量重建：未变技能也送 LLM（绕过门控）。"""
    existing_by_slug = {'translator': _existing()}
    skills = [_skill()]

    recorder, sent = _make_recorder()
    monkeypatch.setattr(mod.translation_service, 'batch_translate_skill_metadata', recorder)

    asyncio.run(
        clawhub_sync_service._batch_prepare_metadata(skills, existing_by_slug, force=True)
    )

    assert len(sent) == 1
    assert {it['name'] for it in sent[0]} == {'翻译大师'}
