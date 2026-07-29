from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GrowthProjectSchemaBase(SchemaBase):
    """平台项目唯一挂靠的获客漏斗基础模型"""

    platform_project_id: UUID = Field(description='平台项目云端权威 UUID，一个平台项目至多一个获客漏斗')
    user_id: int = Field(description='None')
    owner_hasn_id: str = Field(description='主人稳定 HASN ID，由服务端从平台项目和鉴权上下文解析')
    owner_scope: str = Field(description='归属模式 (personal:个人:blue/enterprise:企业:purple)')
    enterprise_id: int | None = Field(None, description='None')
    name: str = Field(description='None')
    tagline: str | None = Field(None, description='None')
    product_profile: dict = Field(description='None')
    icp_profile: dict = Field(description='None')
    profile_version: int = Field(description='None')
    profile_source_hash: str | None = Field(None, description='None')
    profile_updated_time: datetime | None = Field(None, description='None')
    kb_ref: str | None = Field(None, description='知识库资源引用 hasn://knowledge/kbs/{id}')
    landing_site_ref: str | None = Field(None, description='站点资源引用 hasn://publish/sites/{id}')
    owner_agent_id: str | None = Field(None, description='None')
    status: str = Field(
        description='状态 (draft:草稿:gray/active:运行中:green/paused:已暂停:orange/archived:已归档:gray)'
    )
    provision_status: str = Field(
        description='开通状态 (pending:待开始:gray/running:进行中:blue/ready:就绪:green/failed:失败:red)'
    )
    provision_error: dict | None = Field(None, description='None')
    monthly_budget: Decimal | None = Field(None, description='None')
    budget_currency: str = Field(description='None')
    quiet_hours_start: int = Field(description='静默时段开始小时，使用项目时区的 0–23 整点')
    quiet_hours_end: int = Field(description='静默时段结束小时，使用项目时区的 0–23 整点')
    daily_outreach_limit: int = Field(description='项目每日发送成功或人工发送证明的触达上限')
    policy_version: int = Field(description='渠道、静默时段、频控和预算策略版本')
    readiness_snapshot: dict = Field(description='None')
    stats_snapshot: dict = Field(description='None')


class CreateGrowthProjectParam(GrowthProjectSchemaBase):
    """创建平台项目唯一挂靠的获客漏斗参数"""


class UpdateGrowthProjectParam(GrowthProjectSchemaBase):
    """更新平台项目唯一挂靠的获客漏斗参数"""


class DeleteGrowthProjectParam(SchemaBase):
    """删除平台项目唯一挂靠的获客漏斗参数"""

    pks: list[UUID] = Field(description='平台项目唯一挂靠的获客漏斗 ID 列表')


class GetGrowthProjectDetail(GrowthProjectSchemaBase):
    """平台项目唯一挂靠的获客漏斗详情"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_time: datetime
    updated_time: datetime | None = None
