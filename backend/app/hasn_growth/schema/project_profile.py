"""获客项目画像与 Knowledge 绑定的专用业务入参。"""

from typing import Literal

from pydantic import Field

from backend.common.schema import SchemaBase


class BindGrowthKnowledgeBody(SchemaBase):
    """主人绑定或改绑同项目 Knowledge。"""

    kb_id: int = Field(ge=1)
    expected_profile_version: int = Field(ge=1)


class ReviewGrowthProfileSuggestionBody(SchemaBase):
    """主人审阅画像建议。"""

    decision: Literal['accept', 'reject']


class AdoptGrowthPlaybookBody(SchemaBase):
    """主人采用打法当前版本并冻结项目级配置。"""

    expected_playbook_version: int = Field(ge=1)
    configuration: dict = Field(default_factory=dict)
