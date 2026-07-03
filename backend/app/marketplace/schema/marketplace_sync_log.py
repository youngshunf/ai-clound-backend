from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MarketplaceSyncLogSchemaBase(SchemaBase):
    """技能市场同步日志基础模型"""
    sync_type: str = Field(description='同步类型 (github:GitHub同步:blue/clawhub:ClawHub同步:green)')
    status: str = Field(description='同步状态 (success:成功:green/failed:失败:red/partial:部分成功:orange)')
    items_synced: int | None = Field(None, description='成功同步数量')
    items_failed: int | None = Field(None, description='失败数量')
    error_message: str | None = Field(None, description='错误信息')
    git_commit_before: str | None = Field(None, description='同步前的 commit hash')
    git_commit_after: str | None = Field(None, description='同步后的 commit hash')
    started_at: datetime = Field(description='开始时间')
    completed_at: datetime | None = Field(None, description='完成时间')


class CreateMarketplaceSyncLogParam(MarketplaceSyncLogSchemaBase):
    """创建技能市场同步日志参数"""


class UpdateMarketplaceSyncLogParam(SchemaBase):
    """更新技能市场同步日志参数"""
    sync_type: str | None = Field(None, description='同步类型 (github:GitHub同步:blue/clawhub:ClawHub同步:green)')
    status: str | None = Field(None, description='同步状态 (success:成功:green/failed:失败:red/partial:部分成功:orange)')
    items_synced: int | None = Field(None, description='成功同步数量')
    items_failed: int | None = Field(None, description='失败数量')
    error_message: str | None = Field(None, description='错误信息')
    git_commit_before: str | None = Field(None, description='同步前的 commit hash')
    git_commit_after: str | None = Field(None, description='同步后的 commit hash')
    started_at: datetime | None = Field(None, description='开始时间')
    completed_at: datetime | None = Field(None, description='完成时间')


class DeleteMarketplaceSyncLogParam(SchemaBase):
    """删除技能市场同步日志参数"""

    pks: list[int] = Field(description='技能市场同步日志 ID 列表')


class GetMarketplaceSyncLogDetail(MarketplaceSyncLogSchemaBase):
    """技能市场同步日志详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
