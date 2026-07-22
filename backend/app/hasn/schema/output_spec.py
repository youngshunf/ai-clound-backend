"""产出要求 `output_spec` 统一契约（doc35 §0.2，跨 hasn_task / hasn_plan 共用）。

**为什么放 `app/hasn`**：它引用产物系统的 `ArtifactKind` 枚举与 `resource_kind` 语义，
产物系统的家在这里；待办（hasn_plan）与工作流节点（hasn_task）都只是它的消费方。

**为什么要归一**（doc35 §0.1 死锁根因）：历史上两套 `output_spec` 各写各的——
待办用 `{required, expects:[{kind}]}`，工作流用 `{kind, label}`；两边的 `kind` 还各自
指向不同词表，且**都没有真校验**（待办压根不校验，工作流只查一张手写白名单）。
结果分身真产出了、闸却判「未产出」，待办永远完不成。

本契约把两套并成一套（取待办那套更完整的形状：required 开关 + expects 多选一），
判定收敛到 `service/output_gate.py::satisfies` 一个纯函数，两个闸共用。
"""

from __future__ import annotations

from pydantic import ConfigDict, Field, model_validator

from backend.app.hasn.schema.hasn_artifacts import ArtifactKind
from backend.common.schema import SchemaBase


class OutputExpect(SchemaBase):
    """一条产出期望：`artifact_kind` / `resource_kind` **二选一**。

    - `artifact_kind`：非应用资源，按**载体**判（document / image / video / voice / file）。
    - `resource_kind`：应用资源，按 **descriptor.resource_kind** 判（knowledge.base /
      deck.presentation …）——比 `artifact_kind='resource'` 精确得多：后者只答「是个应用资源」，
      答不出「是知识库还是演示文稿」。
    """

    # extra='forbid'：旧形状 `{kind: dataset}` 必须硬报错。若放任 Pydantic 默认忽略未知字段，
    # 旧 spec 会被解析成「expects 里一个全空的期望」→ 退化成「有任意产物就算过」，
    # 比报错更坏：闸看着还在，实际已失效，且没人知道。
    model_config = ConfigDict(extra='forbid')

    artifact_kind: ArtifactKind | None = Field(
        None, description='非应用资源：按载体判（document/image/video/voice/file）'
    )
    resource_kind: str | None = Field(None, description='应用资源：按 descriptor.resource_kind 判（如 knowledge.base）')
    format: str | None = Field(None, description='可选：格式提示（待办原有，仅展示/提示分身，不参与判定）')
    note: str | None = Field(None, description='可选：说明（待办原有，仅展示，不参与判定）')

    @model_validator(mode='after')
    def _exactly_one(self) -> OutputExpect:
        if bool(self.artifact_kind) == bool(self.resource_kind):
            raise ValueError('artifact_kind 与 resource_kind 必须二选一（不能都填、也不能都不填）')
        return self


class OutputSpec(SchemaBase):
    """节点/待办的产出要求。无 spec 或 `required=false` → 不设闸。"""

    model_config = ConfigDict(extra='forbid')

    required: bool = Field(True, description='是否设闸（工作流侧：声明了 expects 即为闸）')
    label: str | None = Field(None, description='可选：产出物的人话名字（工作流原有，展示用）')
    expects: list[OutputExpect] = Field(
        default_factory=list,
        description='期望列表，多条之间是「或」（满足任一即过）。为空 + required=true → 需任意产物',
    )
