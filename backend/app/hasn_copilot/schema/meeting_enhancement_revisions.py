from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MeetingEnhancementRevisionsSchemaBase(SchemaBase):
    """会议会后增强候选 revision（云端权威，含淘汰审计元数据）基础模型"""

    meeting_id: str | UUID = Field(description='所属会议云端权威 ID')
    owner_hasn_id: str = Field(description='归属主人 HASN ID（冗余隔离键，所有查询强制带）')
    operation_id: str = Field(description='daemon 稳定增强操作 ID（同会议幂等）')
    revision_number: int = Field(description='会议内单调递增候选序号')
    supersedes: str | UUID = Field(description='来源 revision 的云端权威 ID（原始实时稿或既有候选）')
    status: str = Field(
        description=(
            '状态 (pending_confirmation:待主人确认:amber/accepted:已接受:green/'
            'rejected:已拒绝:gray/superseded:已被新候选替换:blue/'
            'evicted:已按保留策略淘汰:red)'
        )
    )
    source_record_version: int = Field(description='生成候选所依据的原始实时稿 record_version')
    transcript_json: dict | list | None = Field(None, description='候选转写结果；淘汰后清空，仅保留审计元数据')
    speaker_annotations_json: dict | list | None = Field(None, description='候选说话人标注结果；可选输出失败时可为空')
    alignment_json: dict | list | None = Field(None, description='候选强制对齐结果；可选输出失败时可为空')
    model_run_id: str | None = Field(None, description='本次联合或组合推理的 model_run_id')
    model_evidence_json: dict = Field(description='模型、组件版本、能力结果和错误的结构化证据')
    created_by_agent_hasn_id: str | None = Field(None, description='参与创建候选的分身 HASN ID；纯语音引擎写入时为空')
    work_session_id: str | None = Field(None, description='分身参与时绑定的工作会话 ID')
    replaced_by: str | UUID | None = Field(None, description='替换当前待确认候选的新候选 server_id')
    decision_reason: str | None = Field(None, description='主人拒绝或系统替换时的稳定原因')
    decided_time: datetime | None = Field(None, description='主人接受或拒绝时间')
    eviction_reason: str | None = Field(None, description='淘汰原因；首版固定 retention_limit')
    evicted_time: datetime | None = Field(None, description='按保留策略淘汰时间')


class CreateMeetingEnhancementRevisionsParam(MeetingEnhancementRevisionsSchemaBase):
    """创建会议会后增强候选 revision（云端权威，含淘汰审计元数据）参数"""


class UpdateMeetingEnhancementRevisionsParam(MeetingEnhancementRevisionsSchemaBase):
    """更新会议会后增强候选 revision（云端权威，含淘汰审计元数据）参数"""


class DeleteMeetingEnhancementRevisionsParam(SchemaBase):
    """删除会议会后增强候选 revision（云端权威，含淘汰审计元数据）参数"""

    pks: list[UUID] = Field(description='会议会后增强候选 revision（云端权威，含淘汰审计元数据） ID 列表')


class GetMeetingEnhancementRevisionsDetail(MeetingEnhancementRevisionsSchemaBase):
    """会议会后增强候选 revision（云端权威，含淘汰审计元数据）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_time: datetime
    updated_time: datetime | None = None
