"""管理端 — new-api 用户 Token/额度/用量 Schema"""

from pydantic import Field

from backend.common.schema import SchemaBase


# ========== 管理端概览 ==========

class AdminNewApiUserOverview(SchemaBase):
    """管理端 new-api 用户概览（列表项）"""
    # 唤星用户信息
    huanxing_user_id: int = Field(description='唤星 sys_user.id')
    user_nickname: str | None = Field(default=None, description='用户昵称')
    user_phone: str | None = Field(default=None, description='用户手机号')
    # 订阅信息
    subscription_tier: str | None = Field(default=None, description='订阅等级 (free/basic/pro/enterprise)')
    subscription_status: str | None = Field(default=None, description='订阅状态 (active/expired/cancelled)')
    # new-api 映射
    newapi_user_id: int = Field(description='new-api users.id')
    newapi_token_key_masked: str = Field(description='脱敏 API Key (sk-hx****xxxx)')
    newapi_token_key: str = Field(description='完整 API Key（前端复制用）')
    newapi_token_id: int = Field(description='new-api tokens.id')
    app_code: str = Field(description='应用标识')
    mapping_status: str = Field(description='映射状态 (active/disabled)')
    # new-api 额度
    total_quota: int = Field(default=0, description='总额度')
    used_quota: int = Field(default=0, description='已使用额度')
    remain_quota: int = Field(default=0, description='剩余额度 (total - used)')
    request_count: int = Field(default=0, description='请求次数')


class AdminNewApiUserList(SchemaBase):
    """管理端 new-api 用户列表（分页）"""
    items: list[AdminNewApiUserOverview] = Field(description='用户列表')
    total: int = Field(description='总记录数')


# ========== 额度修改 ==========

class AdminQuotaUpdateParam(SchemaBase):
    """管理员修改用户额度参数"""
    new_quota: int = Field(description='新的总额度')


# ========== 用量统计（复用 llm 模块 schema）==========

class AdminUsageSummaryItem(SchemaBase):
    """用量统计项（按模型分组）"""
    model_name: str = Field(description='模型名称')
    prompt_tokens: int = Field(default=0, description='输入 tokens')
    completion_tokens: int = Field(default=0, description='输出 tokens')
    quota: int = Field(default=0, description='消耗额度')
    request_count: int = Field(default=0, description='请求次数')


class AdminUsageSummary(SchemaBase):
    """用量统计概览"""
    items: list[AdminUsageSummaryItem] = Field(description='按模型分组的用量')
    total_prompt_tokens: int = Field(default=0, description='总输入 tokens')
    total_completion_tokens: int = Field(default=0, description='总输出 tokens')
    total_quota: int = Field(default=0, description='总消耗额度')
    total_requests: int = Field(default=0, description='总请求次数')
    period_start: int = Field(description='统计开始时间 (unix)')
    period_end: int = Field(description='统计结束时间 (unix)')


class AdminUsageDetailItem(SchemaBase):
    """用量明细项"""
    id: int
    created_at: int = Field(description='请求时间 (unix)')
    model_name: str | None = Field(default=None, description='模型名称')
    prompt_tokens: int = Field(default=0, description='输入 tokens')
    completion_tokens: int = Field(default=0, description='输出 tokens')
    quota: int = Field(default=0, description='消耗额度')
    use_time: int = Field(default=0, description='耗时（秒）')
    is_stream: bool = Field(default=False, description='是否流式')
    request_id: str | None = Field(default=None, description='请求 ID')
    token_name: str | None = Field(default=None, description='Token 名称')


class AdminUsageDetail(SchemaBase):
    """用量明细（分页）"""
    items: list[AdminUsageDetailItem] = Field(description='明细列表')
    total: int = Field(description='总记录数')


class AdminQuotaInfo(SchemaBase):
    """指定用户的详细额度信息"""
    huanxing_user_id: int
    newapi_user_id: int
    total_quota: int = Field(description='总额度')
    used_quota: int = Field(description='已使用额度')
    remain_quota: int = Field(description='剩余额度')
    request_count: int = Field(description='请求次数')
