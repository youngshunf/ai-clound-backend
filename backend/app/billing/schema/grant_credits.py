from datetime import datetime
from decimal import Decimal
from pydantic import Field
from backend.common.schema import SchemaBase


class GrantCreditsParam(SchemaBase):
    """管理员赠送积分参数"""

    user_ids: list[int] = Field(description='目标用户 ID 列表（支持批量赠送）')
    amount: Decimal = Field(gt=0, description='赠送积分数量')
    expires_at: datetime | None = Field(None, description='过期时间（不填则永不过期）')
    description: str | None = Field(None, description='赠送说明')


class GrantCreditsResult(SchemaBase):
    """赠送积分结果"""

    success_count: int = Field(description='成功赠送用户数')
    failed_count: int = Field(description='失败用户数')
    total_credits: Decimal = Field(description='赠送总积分')
    details: list[dict] = Field(description='每个用户的结果详情')
