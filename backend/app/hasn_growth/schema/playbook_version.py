from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class PlaybookVersionSchemaBase(SchemaBase):
    """获客打法不可变版本快照，历史执行只读取本基础模型"""

    playbook_id: int = Field(description='None')
    version: int = Field(description='None')
    name: str = Field(description='None')
    goal: str | None = Field(None, description='None')
    target_profile: dict = Field(description='None')
    cadence: dict = Field(description='None')
    tone_guide: str | None = Field(None, description='None')
    exit_rule: dict = Field(description='None')
    definition_hash: str = Field(description='规范化打法定义 SHA256，用于版本幂等与审计')
    created_by_kind: str = Field(description='None')
    created_by_id: str | None = Field(None, description='None')


class CreatePlaybookVersionParam(PlaybookVersionSchemaBase):
    """创建获客打法不可变版本快照，历史执行只读取本参数"""


class UpdatePlaybookVersionParam(PlaybookVersionSchemaBase):
    """更新获客打法不可变版本快照，历史执行只读取本参数"""


class DeletePlaybookVersionParam(SchemaBase):
    """删除获客打法不可变版本快照，历史执行只读取本参数"""

    pks: list[int] = Field(description='获客打法不可变版本快照，历史执行只读取本 ID 列表')


class GetPlaybookVersionDetail(PlaybookVersionSchemaBase):
    """获客打法不可变版本快照，历史执行只读取本详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
