from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ContentInsightSchemaBase(SchemaBase):
    """内容洞察（复盘结构化结论，进化沉淀核心）基础模型"""
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    created_by_agent_id: str | None = Field(None, description='None')
    period: str | None = Field(None, description='复盘周期（2026-W24 周报 / content:{id} 单篇）')
    insight_type: str = Field(description='类型 (pillar_performance:支柱表现:blue/hook_pattern:钩子套路:purple/timing:发布时间:orange/audience:受众:cyan/lesson:教训:gray)')
    summary: str = Field(description='None')
    evidence_json: dict = Field(description='数据证据（哪些 content/publish 的哪些指标支撑）')
    action_taken: dict = Field(description='已据此采取的动作（调了哪个 pillar_weight / 加了哪条 viral_pattern / 改了 playbook）')
    confidence: Decimal = Field(description='置信度（样本量小时低，不轻易大改）')


class CreateContentInsightParam(ContentInsightSchemaBase):
    """创建内容洞察（复盘结构化结论，进化沉淀核心）参数"""


class UpdateContentInsightParam(ContentInsightSchemaBase):
    """更新内容洞察（复盘结构化结论，进化沉淀核心）参数"""


class DeleteContentInsightParam(SchemaBase):
    """删除内容洞察（复盘结构化结论，进化沉淀核心）参数"""

    pks: list[int] = Field(description='内容洞察（复盘结构化结论，进化沉淀核心） ID 列表')


class GetContentInsightDetail(ContentInsightSchemaBase):
    """内容洞察（复盘结构化结论，进化沉淀核心）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
