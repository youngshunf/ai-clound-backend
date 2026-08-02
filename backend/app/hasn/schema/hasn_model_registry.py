from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

# `model_name` / `model_ratio` 撞 pydantic 的 `model_` 保护命名空间（会刷 UserWarning），
# 但字段名必须与 new-api 的网关字段一致，故在本模块整体解除该保护。
_MODEL_NS = ConfigDict(protected_namespaces=())


class HasnModelRegistrySchemaBase(SchemaBase):
    """模型注册表基础模型"""

    model_config = _MODEL_NS

    model_name: str = Field(description='网关上的模型名（同步键，全局唯一）')
    capability: str = Field(description='能力类别 (chat/vision/image_generate/image_edit/tts/stt/video/embedding/rerank/unclassified)')
    inputs: dict = Field(description='输入要求表，每种输入取 required/optional/unsupported，省略即 unsupported；text 恒为必需不写')
    dialect: str | None = Field(None, description='入参方言 (openai/ali)')
    quality: str | None = Field(None, description='质量档 (draft/standard/high)')
    scenario: str | None = Field(None, description='适用场景一句话（给分身选型看）')
    agent_visible: bool = Field(description='是否对分身暴露（新同步进来的默认关闭，标注后再放开）')
    sort_order: int = Field(description='同能力内的推荐顺序（failover 优先级，小的在前）')
    vendor_name: str | None = Field(None, description='供应商显示名（来自 new-api）')
    relative_cost: Decimal | None = Field(None, description='new-api model_ratio 快照，仅内部/Admin 可见，绝不下发')
    cost_extra: dict = Field(description='new-api 其它计费参数原样留档，不下发')
    cost_tier_override: str | None = Field(None, description='人工覆盖价格档位 (economy/standard/premium)，留空即用算出来的')
    enable_groups: list[str] = Field(description='可用分组（来自 new-api enable_groups）')
    upstream_status: str = Field(description='网关状态 (active:网关上可用/missing:网关上已消失)')
    last_synced_time: datetime | None = Field(None, description='最近一次在网关上被看到的时间')


class CreateHasnModelRegistryParam(HasnModelRegistrySchemaBase):
    """创建模型注册表参数（**仅同步器内部使用**，不暴露 Admin 端点）。

    注册表的行只能来自 new-api 同步。放开手工创建等于放开「手输一个网关上不存在的模型名」
    ——那正是 2026-08-02 线上视频全线 503 的根因，本设计要消灭的就是它。
    """


class UpdateHasnModelRegistryParam(HasnModelRegistrySchemaBase):
    """整体更新参数（codegen 产物，保留给 CRUD 层；Admin 面只开 PATCH 标注列）。"""


class DeleteHasnModelRegistryParam(SchemaBase):
    """删除参数（**不暴露 Admin 端点**：同步语义是「绝不删行」，删了人工标注就得重标）。"""

    pks: list[int] = Field(description='模型注册表 ID 列表')


class PatchModelAnnotationParam(SchemaBase):
    """人工标注列的局部更新（Admin 唯一写入面）。

    只允许改**语义列**——new-api 权威的成本/分组/供应商/网关状态由同步器覆盖，人工改了下轮
    就被冲掉，放开只会制造「改了没生效」的困惑。字段一律可选：未传即不动该列。
    """

    model_config = _MODEL_NS

    capability: str | None = Field(None, description='能力类别；须是合法枚举值')
    inputs: dict[str, str] | None = Field(None, description='输入要求表，值只允许 required/optional/unsupported')
    dialect: str | None = Field(None, description='入参方言 openai/ali；传空串表示清空')
    quality: str | None = Field(None, description='质量档 draft/standard/high；传空串表示清空')
    scenario: str | None = Field(None, description='适用场景一句话；传空串表示清空')
    agent_visible: bool | None = Field(None, description='是否对分身暴露')
    sort_order: int | None = Field(None, description='同能力内推荐顺序（小的在前）')
    cost_tier_override: str | None = Field(None, description='覆盖价格档位 economy/standard/premium；传空串表示清空（回落自动算）')


class GetHasnModelRegistryDetail(HasnModelRegistrySchemaBase):
    """模型注册表详情（Admin 面出参）。"""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    created_time: datetime
    updated_time: datetime | None = None


class ModelRegistryRow(GetHasnModelRegistryDetail):
    """Admin 列表行 = 详情 + 只读辅助列。

    `suggested_capability` 是**建议值**（按模型名 + 端点类型推断），供运营一键采纳，
    **绝不自动写库**——靠名字猜在我们自己的网关上已被证伪（设计 §4.3）。
    """

    suggested_capability: str = Field(description='能力类别建议值（只读辅助，需人工确认后才生效）')


class ModelRegistrySyncReportSchema(SchemaBase):
    """一轮同步的真实结果。"""

    created: int = Field(description='本轮新增（capability 待标注）')
    updated: int = Field(description='既有模型里 new-api 列确有变化的数量')
    missing: int = Field(description='本轮网关上没出现、已标 missing 的数量（行仍保留）')
    upstream_total: int = Field(description='本轮网关返回的模型总数')
    unclassified: int = Field(description='当前仍待标注 capability 的模型数')
