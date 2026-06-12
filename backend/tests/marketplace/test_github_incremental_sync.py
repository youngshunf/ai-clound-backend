"""GitHub 同步：增量化（路径级 + 元数据变更门控）单测。

锁住本次「webhook 不再每次全量重扫 + 全量重译」改造的契约：

- ``collect_changed_paths``：从 push commits 收集增删改文件路径并集。
- ``skill_dir_touched``：技能目录是否被本次改动命中（SKILL.md / references / icon 都算）。
- ``submodule_refresh_needed``：仅 .gitmodules 或子模块 gitlink 指针变更才刷新子模块。
- ``common_skills_changed`` / ``bundles_changed``：是否触发对账 / 技能包重扫。
- ``metadata_unchanged`` / ``translation_from_existing``：name/desc/tags 未变 → 复用缓存译文。
- ``_batch_translate`` 真实分流：只把改动 / 新增技能送 LLM（用 recorder 断言未变技能零调用）。
"""
from __future__ import annotations

import asyncio
import json

from types import SimpleNamespace

from backend.app.marketplace.service import github_sync_service as mod
from backend.app.marketplace.service.github_sync_service import (
    bundles_changed,
    collect_changed_paths,
    common_skills_changed,
    metadata_unchanged,
    skill_dir_touched,
    submodule_refresh_needed,
    translation_from_existing,
)


def _commit(added=None, modified=None, removed=None) -> dict:
    return {'added': added or [], 'modified': modified or [], 'removed': removed or []}


# ---------- collect_changed_paths ----------

def test_collect_changed_paths_unions_added_modified_removed():
    commits = [
        _commit(added=['huanxing-skills/official/a/SKILL.md']),
        _commit(modified=['common-skills.yaml'], removed=['huanxing-skills/x/b/SKILL.md']),
    ]
    assert collect_changed_paths(commits) == {
        'huanxing-skills/official/a/SKILL.md',
        'common-skills.yaml',
        'huanxing-skills/x/b/SKILL.md',
    }


def test_collect_changed_paths_empty_and_garbage():
    assert collect_changed_paths([]) == set()
    assert collect_changed_paths([{'added': [None, '', 'ok']}]) == {'ok'}


# ---------- skill_dir_touched ----------

def test_skill_dir_touched_matches_file_inside_dir():
    changed = {'huanxing-skills/official/hasn-community/SKILL.md'}
    assert skill_dir_touched('huanxing-skills/official/hasn-community', changed) is True
    # references / icon under the dir also count
    assert skill_dir_touched(
        'huanxing-skills/official/hasn-community',
        {'huanxing-skills/official/hasn-community/references/x.md'},
    ) is True


def test_skill_dir_touched_not_matched_by_sibling_prefix():
    # foo vs foo-bar 不能被前缀误命中
    changed = {'huanxing-skills/official/hasn-community-extra/SKILL.md'}
    assert skill_dir_touched('huanxing-skills/official/hasn-community', changed) is False


def test_skill_dir_touched_empty_repo_path():
    assert skill_dir_touched('', {'anything'}) is False


# ---------- submodule / reconcile gates ----------

def test_submodule_refresh_needed_only_on_gitmodules_or_gitlink():
    assert submodule_refresh_needed({'.gitmodules'}) is True
    assert submodule_refresh_needed({'github/baoyu-skills'}) is True  # gitlink 指针
    # 普通主仓技能 push 不该触发子模块刷新（根治卡死的关键）
    assert submodule_refresh_needed({'huanxing-skills/official/a/SKILL.md'}) is False
    # 子模块内部文件路径（更深）不是 gitlink，不触发
    assert submodule_refresh_needed({'github/baoyu-skills/skills/x/SKILL.md'}) is False


def test_common_and_bundles_change_gates():
    assert common_skills_changed({'common-skills.yaml'}) is True
    assert common_skills_changed({'huanxing-skills/official/a/SKILL.md'}) is False
    assert bundles_changed({'common-bundles.yaml'}) is True
    assert bundles_changed({'bundles/backend-dev/bundle.yaml'}) is True
    assert bundles_changed({'huanxing-skills/official/a/SKILL.md'}) is False


# ---------- metadata change-gate ----------

def _row(**kw) -> SimpleNamespace:
    base = dict(
        skill_id='huanxing/official/a',
        name='社区助手',
        source_language='zh',
        description_en='Community helper',
        description_zh='社区助手描述',
        name_en='Community Assistant',
        name_zh='社区助手',
        tags='["社区", "助手"]',
        tags_en='["community", "helper"]',
        tags_zh='["社区", "助手"]',
        emoji='🤝',
        category='communication',
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _scanned(**kw) -> dict:
    base = dict(name='社区助手', description='社区助手描述', tag_hints=['社区', '助手'])
    base.update(kw)
    return base


def test_metadata_unchanged_true_when_source_matches_and_translations_present():
    assert metadata_unchanged(_scanned(), _row()) is True


def test_metadata_unchanged_false_when_name_changed():
    assert metadata_unchanged(_scanned(name='社区超级助手'), _row()) is False


def test_metadata_unchanged_false_when_description_changed():
    assert metadata_unchanged(_scanned(description='改过的描述'), _row()) is False


def test_metadata_unchanged_false_when_tags_changed():
    assert metadata_unchanged(_scanned(tag_hints=['社区']), _row()) is False


def test_metadata_unchanged_false_when_translation_missing():
    assert metadata_unchanged(_scanned(), _row(name_en=None)) is False
    assert metadata_unchanged(_scanned(), _row(description_zh=None)) is False


def test_metadata_unchanged_false_when_no_existing():
    assert metadata_unchanged(_scanned(), None) is False


def test_translation_from_existing_shapes_like_llm_output():
    out = translation_from_existing(_row())
    assert out['name_en'] == 'Community Assistant'
    assert out['name_zh'] == '社区助手'
    assert out['tags_en'] == ['community', 'helper']
    assert out['tags_zh'] == ['社区', '助手']
    assert out['emoji'] == '🤝'
    assert out['category'] == 'communication'
    assert out['source_language'] == 'zh'


# ---------- _batch_translate 真实分流：只译改动的 ----------

class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeDB:
    """最小 AsyncSession 替身：execute 返回预置的现有行（按 skill_id 过滤无关，全返）。"""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._rows)


def test_batch_translate_only_sends_changed_skills_to_llm(monkeypatch):
    # 库内已有 a（未变）、b（描述变了）；c 是新增（库内无）。
    existing = [
        _row(skill_id='huanxing/official/a'),
        _row(
            skill_id='huanxing/official/b', name='另一个技能',
            description_zh='旧描述', name_en='Other', name_zh='另一个技能',
            description_en='Old desc', tags='[]', tags_en='[]', tags_zh='[]',
        ),
    ]
    skills_data = [
        {'skill_id': 'huanxing/official/a', 'name': '社区助手',
         'description': '社区助手描述', 'tag_hints': ['社区', '助手'], 'source_language': 'zh'},
        {'skill_id': 'huanxing/official/b', 'name': '另一个技能',
         'description': '新描述（变了）', 'tag_hints': [], 'source_language': 'zh'},
        {'skill_id': 'huanxing/official/c', 'name': 'New Skill',
         'description': 'Brand new', 'tag_hints': ['x'], 'source_language': 'en'},
    ]

    sent: list[list[dict]] = []

    async def _recorder(items, *, concurrency=4, categories=None):  # noqa: ARG001
        sent.append(items)
        # 返回与输入对齐的「译文」占位，结构与真实输出一致即可。
        return [
            {
                'name_en': it['name'], 'name_zh': it['name'],
                'description_en': it['description'], 'description_zh': it['description'],
                'tags_en': [], 'tags_zh': [], 'emoji': None,
                'category': 'other', 'source_language': it.get('source_lang'),
            }
            for it in items
        ]

    monkeypatch.setattr(mod.translation_service, 'batch_translate_skill_metadata', _recorder)

    results = asyncio.run(mod.github_sync_service._batch_translate(_FakeDB(existing), skills_data))

    # 只 b、c 被送 LLM（a 未变复用缓存）；a 的结果来自现有行。
    assert len(sent) == 1
    sent_names = [it['name'] for it in sent[0]]
    assert sent_names == ['另一个技能', 'New Skill']
    assert len(results) == 3
    # a（index 0）复用现有译文
    assert results[0]['name_en'] == 'Community Assistant'
    assert results[0]['description_zh'] == '社区助手描述'
    # b（index 1）走 LLM，拿到新描述
    assert results[1]['description_zh'] == '新描述（变了）'
    # c（index 2）新增，走 LLM
    assert results[2]['name_en'] == 'New Skill'


def test_batch_translate_all_cached_zero_llm(monkeypatch):
    """全部未变 → 一次 LLM 都不调（核心省钱点）。"""
    existing = [_row(skill_id='huanxing/official/a')]
    skills_data = [
        {'skill_id': 'huanxing/official/a', 'name': '社区助手',
         'description': '社区助手描述', 'tag_hints': ['社区', '助手'], 'source_language': 'zh'},
    ]

    called = {'n': 0}

    async def _recorder(items, *, concurrency=4, categories=None):  # noqa: ARG001
        called['n'] += 1
        return []

    monkeypatch.setattr(mod.translation_service, 'batch_translate_skill_metadata', _recorder)
    results = asyncio.run(mod.github_sync_service._batch_translate(_FakeDB(existing), skills_data))

    assert called['n'] == 0
    assert results[0]['name_en'] == 'Community Assistant'
