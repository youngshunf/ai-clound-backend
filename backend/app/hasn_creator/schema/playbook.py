from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class PlaybookSchemaBase(SchemaBase):
    """获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义基础模型"""
    user_id: int | None = Field(None, description='归属主人（可空=内置 playbook）')
    name: str = Field(description='None')
    enabled: bool = Field(description='None')
    goal: str | None = Field(None, description='None')
    target_profile: dict = Field(description='None')
    cadence: dict = Field(description='触达节奏 [{day,channel,goal}]')
    tone_guide: str | None = Field(None, description='None')
    exit_rule: dict = Field(description='止损规则 {max_silent_rounds,action}')
    is_builtin: bool = Field(description='None')


class CreatePlaybookParam(PlaybookSchemaBase):
    """创建获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义参数"""


class UpdatePlaybookParam(PlaybookSchemaBase):
    """更新获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义参数"""


class DeletePlaybookParam(SchemaBase):
    """删除获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义参数"""

    pks: list[int] = Field(description='获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义 ID 列表')


class GetPlaybookDetail(PlaybookSchemaBase):
    """获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
