"""主人画像完整度 schema（hasn_memory.owner_profile_coverage）。

owner 读端点 + 判定服务用的对外结构。设计事实源：
docs/hasn-node设计文档/19-规划与目标管理/03-了解主人：采访建档·完整度判定·主动规划闭环设计.md §4.1/§5.3
"""

from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class OwnerProfileCoverageSchemaBase(SchemaBase):
    """主人画像完整度基础模型（落库参数复用）。"""

    owner_id: str = Field(description='主人 hasn_id（hasn_humans.hasn_id）')
    dimension: str = Field(description='画像维度 (interests/work/residence/goals/life_plan)')
    status: str = Field(description='覆盖状态 (missing/partial/sufficient)')
    confidence: Decimal = Field(description='LLM 打分置信度 0–1')
    summary: str | None = Field(None, description='该维度已知信息的一句话摘要')
    missing_hint: str | None = Field(None, description='该维度还缺什么')
    evidence_version: int = Field(description='判定所依据的 owner_memory.version')
    assessed_time: datetime | None = Field(None, description='最近一次判定时间')


class CreateOwnerProfileCoverageParam(OwnerProfileCoverageSchemaBase):
    """创建主人画像完整度参数。"""


class UpdateOwnerProfileCoverageParam(OwnerProfileCoverageSchemaBase):
    """更新主人画像完整度参数。"""


# ---- 对外（owner 读 / 判定服务返回）----


class DimensionCoverage(SchemaBase):
    """单维度完整度（面向主人 / 采访分身）。"""

    dimension: str = Field(description='维度 key (interests/work/residence/goals/life_plan)')
    label: str = Field(description='维度中文名')
    status: str = Field(description='覆盖状态 (missing/partial/sufficient)')
    confidence: float = Field(0.0, description='LLM 打分置信度 0–1')
    summary: str | None = Field(None, description='已知信息一句话摘要（驱动"已了解"）')
    missing_hint: str | None = Field(None, description='还缺什么（驱动采访提问 / "还需了解"）')


class OwnerProfileCoverageResponse(SchemaBase):
    """主人画像完整度聚合结果——驱动首页入口显隐与采访目标。"""

    dimensions: list[DimensionCoverage] = Field(description='5 个维度的覆盖状态（固定顺序）')
    all_sufficient: bool = Field(description='5 维是否全部 sufficient（true → 首页入口隐藏 + 开启主动闭环）')
    sufficient_count: int = Field(description='已充分的维度数')
    total: int = Field(description='维度总数（固定 5）')
    next_dimensions: list[str] = Field(description='尚未充分的维度 key（驱动"缺什么采访什么"）')
    memory_version: int = Field(0, description='当前 owner_memory 版本（判定依据）')


class GetOwnerProfileCoverageDetail(OwnerProfileCoverageSchemaBase):
    """主人画像完整度详情（带主键/时间，调试用）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
