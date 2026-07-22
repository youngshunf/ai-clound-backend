"""产出要求契约 + 判定纯函数守卫（doc35 B1 §0.2）。

钉死三件事，任缺一件就会退回 §0.1 的死锁：
1. **旧形状必须硬报错**——`{kind: 'dataset'}` 静默被接受 = 闸看着还在、实际已失效；
2. **二选一必须强制**——两个都填/都不填的期望，判定语义无解；
3. **judge 语义精确**——`resource_kind` 判「是什么资源」，`artifact_kind` 判「什么载体」，别混。

纯函数无 IO，故不连库；registry 存在性校验落在 test_workflow_template_service 那侧（需 manifest）。
"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from backend.app.hasn.schema.output_spec import OutputExpect, OutputSpec
from backend.app.hasn.service.output_gate import describe_expects, satisfies


def _artifact(kind: str, resource_kind: str | None = None) -> dict:
    """产物的最小形状（判定只看这两个维度）。dict 与 ORM 行两种形状都要能喂。"""
    return {'kind': kind, 'resource_kind': resource_kind}


class TestOutputExpectValidation:
    def test_rejects_legacy_kind_field(self) -> None:
        """旧形状 `{kind: dataset}` → 硬报错。

        这是 E1a（模板改写）必须排在 B1 之前的原因：extra='forbid' 会让存量旧 spec 直接炸。
        """
        with pytest.raises(ValidationError):
            OutputExpect.model_validate({'kind': 'dataset'})

    def test_rejects_both_kinds(self) -> None:
        with pytest.raises(ValidationError, match='二选一'):
            OutputExpect.model_validate({'artifact_kind': 'document', 'resource_kind': 'knowledge.base'})

    def test_rejects_neither_kind(self) -> None:
        """两个都不填 → 一个「什么都不期望」的期望，判定无解，必须拒。"""
        with pytest.raises(ValidationError, match='二选一'):
            OutputExpect.model_validate({'note': '随便产点什么'})

    def test_rejects_unknown_artifact_kind(self) -> None:
        """`deck`/`webpage`/`dataset`/`other` 已在 doc35 §3 砍掉，Literal 自带拦截。"""
        for stale in ('deck', 'webpage', 'dataset', 'other'):
            with pytest.raises(ValidationError):
                OutputExpect.model_validate({'artifact_kind': stale})

    def test_accepts_resource_kind_with_optional_hints(self) -> None:
        expect = OutputExpect.model_validate({
            'resource_kind': 'knowledge.base',
            'format': 'md',
            'note': '按主题分目录',
        })
        assert expect.resource_kind == 'knowledge.base'
        assert expect.artifact_kind is None

    def test_spec_forbids_extra_keys(self) -> None:
        with pytest.raises(ValidationError):
            OutputSpec.model_validate({'required': True, 'kind': 'deck'})


class TestSatisfies:
    def test_no_spec_passes(self) -> None:
        assert satisfies(None, []) is True

    def test_not_required_passes_even_with_nothing(self) -> None:
        spec = OutputSpec.model_validate({'required': False, 'expects': [{'artifact_kind': 'document'}]})
        assert satisfies(spec, []) is True

    def test_required_without_expects_needs_any_artifact(self) -> None:
        spec = OutputSpec.model_validate({'required': True})
        assert satisfies(spec, []) is False
        assert satisfies(spec, [_artifact('file')]) is True

    def test_resource_kind_matches_exactly(self) -> None:
        """§0.1 死锁链的正向回归：分身真建库 → 登记 resource_kind=knowledge.base → 闸放行。"""
        spec = OutputSpec.model_validate({'required': True, 'expects': [{'resource_kind': 'knowledge.base'}]})
        assert satisfies(spec, [_artifact('resource', 'knowledge.base')]) is True

    def test_resource_kind_does_not_match_other_resource(self) -> None:
        """建了知识库 ≠ 交了演示文稿。`artifact_kind` 两者都是 resource，判不出差别——正是要 resource_kind 的原因。"""
        spec = OutputSpec.model_validate({'required': True, 'expects': [{'resource_kind': 'deck.presentation'}]})
        assert satisfies(spec, [_artifact('resource', 'knowledge.base')]) is False

    def test_artifact_kind_matches_non_resource(self) -> None:
        spec = OutputSpec.model_validate({'required': True, 'expects': [{'artifact_kind': 'document'}]})
        assert satisfies(spec, [_artifact('document')]) is True
        assert satisfies(spec, [_artifact('image')]) is False

    def test_expects_are_or_semantics(self) -> None:
        spec = OutputSpec.model_validate({
            'required': True,
            'expects': [{'artifact_kind': 'video'}, {'resource_kind': 'knowledge.base'}],
        })
        assert satisfies(spec, [_artifact('resource', 'knowledge.base')]) is True
        assert satisfies(spec, [_artifact('video')]) is True
        assert satisfies(spec, [_artifact('image')]) is False

    def test_matches_across_multiple_artifacts(self) -> None:
        """一堆产物里只要有一条命中即过（分身通常顺手产一串附带物）。"""
        spec = OutputSpec.model_validate({'required': True, 'expects': [{'resource_kind': 'deck.presentation'}]})
        items = [_artifact('image'), _artifact('file'), _artifact('resource', 'deck.presentation')]
        assert satisfies(spec, items) is True

    def test_accepts_object_shaped_artifacts(self) -> None:
        """ORM 行 / schema 对象（非 dict）也要能判——两个闸喂进来的形状不一样。"""

        class _Row:
            kind = 'resource'
            resource_kind = 'knowledge.base'

        spec = OutputSpec.model_validate({'required': True, 'expects': [{'resource_kind': 'knowledge.base'}]})
        assert satisfies(spec, [_Row()]) is True

    def test_resource_artifact_without_resource_kind_never_matches(self) -> None:
        """`kind=resource` 但 `resource_kind` 空 = 登记侧漏了——诚实判不过，别猜。"""
        spec = OutputSpec.model_validate({'required': True, 'expects': [{'resource_kind': 'knowledge.base'}]})
        assert satisfies(spec, [_artifact('resource', None)]) is False


class TestDescribeExpects:
    """闸拒绝时的文案（B2）：说给**主人**听，不能把 `deck.presentation` 甩到他脸上。"""

    def test_label_wins(self) -> None:
        spec = OutputSpec.model_validate({
            'required': True,
            'label': '市场分析知识库',
            'expects': [{'resource_kind': 'knowledge.base'}],
        })
        assert describe_expects(spec) == '市场分析知识库'

    def test_resource_kind_renders_manifest_verb(self) -> None:
        """展示名取 manifest 的 `descriptor.card.verb`——与完成卡标题同源，主人两处看到同一个词。"""
        spec = OutputSpec.model_validate({'required': True, 'expects': [{'resource_kind': 'deck.presentation'}]})
        text = describe_expects(spec)
        assert 'deck.presentation' not in text, '内部键不该出现在给主人的文案里'
        assert text == '演示文稿'

    def test_artifact_kind_renders_chinese_label(self) -> None:
        spec = OutputSpec.model_validate({'required': True, 'expects': [{'artifact_kind': 'document'}]})
        assert describe_expects(spec) == '文档'

    def test_multiple_expects_joined_by_or(self) -> None:
        """expects 是「或」语义，文案就得写「或」——顿号会读成「都要交」。"""
        spec = OutputSpec.model_validate({
            'required': True,
            'expects': [{'artifact_kind': 'document'}, {'artifact_kind': 'video'}],
        })
        assert describe_expects(spec) == '文档或视频'

    def test_dedupes_same_label(self) -> None:
        spec = OutputSpec.model_validate({
            'required': True,
            'expects': [{'artifact_kind': 'document', 'format': 'md'}, {'artifact_kind': 'document'}],
        })
        assert describe_expects(spec) == '文档'

    def test_no_expects_falls_back(self) -> None:
        spec = OutputSpec.model_validate({'required': True})
        assert describe_expects(spec) == '要求的产物'
