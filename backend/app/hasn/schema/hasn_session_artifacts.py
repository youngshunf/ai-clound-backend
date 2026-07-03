from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnSessionArtifactsSchemaBase(SchemaBase):
    """HASN 会话产物基础模型"""
    session_id: str = Field(description='会话 ID')
    artifact_kind: str = Field(description='产物类型 (file/code/report/data)')
    artifact_name: str | None = Field(None, description='产物名称')
    artifact_path: str | None = Field(None, description='产物路径')
    summary_json: str | None = Field(None, description='产物摘要 (JSON)')
    sync_policy: str = Field(description='同步策略 (full/metadata_only/local_only)')


class CreateHasnSessionArtifactsParam(HasnSessionArtifactsSchemaBase):
    """创建HASN 会话产物参数"""


class UpdateHasnSessionArtifactsParam(HasnSessionArtifactsSchemaBase):
    """更新HASN 会话产物参数"""


class DeleteHasnSessionArtifactsParam(SchemaBase):
    """删除HASN 会话产物参数"""

    pks: list[int] = Field(description='HASN 会话产物 ID 列表')


class GetHasnSessionArtifactsDetail(HasnSessionArtifactsSchemaBase):
    """HASN 会话产物详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
