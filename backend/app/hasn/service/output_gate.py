"""产出闸判定纯函数（doc35 §0.2）——待办完成闸与工作流节点产出闸**共用同一份**。

历史上两个闸各写各的判定（待办 `kind in expected_kinds`、daemon 工作流 `artifact_kind == kind`），
词表还各自漂移，于是「分身真产出了却判未产出」在两条链上各犯一次。收敛成一个纯函数后，
契约只有一处；daemon Rust 侧同构实现一份（`crates/hasn-node/src/runtime/output_gate.rs`），
两边各自单测钉死同一套语义。

判定语义（§0.2）：
- 无 spec 或 `required=false` → 直过；
- `expects` 为空 + `required=true` → 需任意产物；
- 否则 expects 之间是「或」：任一期望被任一产物命中即过。
"""

from __future__ import annotations

from typing import Any, Protocol

from backend.app.hasn.schema.output_spec import OutputSpec


class _ArtifactLike(Protocol):
    """判定只需要产物的两个类型维度——ORM 行、schema 对象、dict 都能喂进来。"""

    kind: Any
    resource_kind: Any


def _kind_of(artifact: Any) -> str | None:
    """取产物的 artifact_kind（「怎么打开」）。dict 与对象两种形状都认。"""
    value = artifact.get('kind') if isinstance(artifact, dict) else getattr(artifact, 'kind', None)
    return str(value) if value else None


def _resource_kind_of(artifact: Any) -> str | None:
    """取产物的 resource_kind（「是什么」）。仅应用资源有值，其余 None。"""
    value = artifact.get('resource_kind') if isinstance(artifact, dict) else getattr(artifact, 'resource_kind', None)
    return str(value) if value else None


def satisfies(spec: OutputSpec | None, artifacts: list[Any]) -> bool:
    """产物集合是否满足产出要求。

    :param spec: 已校验的 `OutputSpec`（None = 无要求）
    :param artifacts: 该待办/节点名下的 active 产物（`hasn_artifacts` 行或等价形状）
    """
    if spec is None or not spec.required:
        return True
    if not spec.expects:
        return len(artifacts) > 0
    return any(
        (expect.artifact_kind is not None and _kind_of(artifact) == expect.artifact_kind)
        or (expect.resource_kind is not None and _resource_kind_of(artifact) == expect.resource_kind)
        for expect in spec.expects
        for artifact in artifacts
    )


# 非应用资源的载体展示名。应用资源的展示名不在这里——它由各 app manifest 的
# `descriptor.card.verb` 声明（单源，见 `ai_native_app_registry.resource_kind_labels`）。
_ARTIFACT_KIND_LABELS: dict[str, str] = {
    'document': '文档',
    'image': '图片',
    'video': '视频',
    'voice': '语音',
    'file': '文件',
    'resource': '应用资源',
}


def describe_expects(spec: OutputSpec) -> str:
    """把产出要求译成给**主人**看的人话（闸拒绝时的文案）。

    主人不认识 `deck.presentation`——报错必须说「演示文稿」。`label` 是编排时人写的，最贴切，优先用；
    否则由 expects 派生。多条 expects 是「或」，用「或」连接，别用顿号（顿号读起来像「都要」）。
    """
    if spec.label:
        return spec.label
    from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

    labels = ai_native_app_registry.resource_kind_labels()
    names: list[str] = []
    for expect in spec.expects:
        if expect.resource_kind:
            # 未登记的 resource_kind 理论上进不来（发布期已拒），真漏了就如实回显键，别编。
            names.append(labels.get(expect.resource_kind, expect.resource_kind))
        elif expect.artifact_kind:
            names.append(_ARTIFACT_KIND_LABELS.get(expect.artifact_kind, expect.artifact_kind))
    # 去重保序：两条 expects 可能同名（如都要文档，只是 format 不同）
    unique = list(dict.fromkeys(names))
    return '或'.join(unique) if unique else '要求的产物'
