from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnJudgeVerdictSchemaBase(SchemaBase):
    """通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）基础模型"""
    judge_kind: str = Field(description='裁判类型 (termination:会话终止:blue/disclosure:隐私披露:green)')
    owner_hasn_id: str = Field(description='发起方分身所属主人 hasn_id（凭据/计费归属）')
    agent_hasn_id: str = Field(description='发起方分身 hasn_id')
    peer_hasn_id: str = Field(description='对端 hasn_id（人或分身）')
    conversation_ref: str = Field(description='daemon 本地会话 id，仅溯源元数据，不作资源解析（URI 铁律豁免范围）')
    input_json: dict = Field(description='脱敏后裁判输入（transcript/正文+上下文；L1 命中片段以 PartialMask 形态入库，不存附件/原文）')
    verdict_json: dict = Field(description='裁判出参 JSON（kind 专属：termination={should_end,reason}；disclosure={allow,categories,reason}）')
    model: str | None = Field(None, description='实际命中的裁判模型名')
    latency_ms: int | None = Field(None, description='LLM 调用耗时（毫秒）')


class CreateHasnJudgeVerdictParam(HasnJudgeVerdictSchemaBase):
    """创建通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）参数"""


class UpdateHasnJudgeVerdictParam(HasnJudgeVerdictSchemaBase):
    """更新通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）参数"""


class DeleteHasnJudgeVerdictParam(SchemaBase):
    """删除通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）参数"""

    pks: list[int] = Field(description='通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表） ID 列表')


class GetHasnJudgeVerdictDetail(HasnJudgeVerdictSchemaBase):
    """通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
