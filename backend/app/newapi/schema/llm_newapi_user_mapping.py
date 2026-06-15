"""唤星用户与 new-api 用户映射 Schema + 额度/用量查询 Schema"""

from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


# ========== 映射表基础 CRUD Schema ==========

class LlmNewapiUserMappingSchemaBase(SchemaBase):
    """唤星用户与 new-api 用户映射基础模型"""
    huanxing_user_id: int = Field(description='唤星 sys_user.id')
    newapi_user_id: int = Field(description='new-api users.id')
    newapi_token_key: str = Field(description='new-api tokens.key（用户默认 API Key）')
    newapi_token_id: int = Field(description='new-api tokens.id')
    app_code: str = Field(description='应用标识 (huanxing:唤星/zhixiaoya:知小鸦)')
    status: str = Field(description='状态 (active:启用:green/disabled:禁用:red)')


class CreateLlmNewapiUserMappingParam(LlmNewapiUserMappingSchemaBase):
    """创建唤星用户与 new-api 用户映射参数"""


class UpdateLlmNewapiUserMappingParam(LlmNewapiUserMappingSchemaBase):
    """更新唤星用户与 new-api 用户映射参数"""


class DeleteLlmNewapiUserMappingParam(SchemaBase):
    """删除唤星用户与 new-api 用户映射参数"""
    pks: list[int] = Field(description='唤星用户与 new-api 用户映射 ID 列表')


class GetLlmNewapiUserMappingDetail(LlmNewapiUserMappingSchemaBase):
    """唤星用户与 new-api 用户映射详情"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None


# ========== new-api 额度/用量查询 Schema ==========

class NewApiQuotaInfo(SchemaBase):
    """new-api 额度信息"""
    total_quota: int = Field(description='总额度')
    used_quota: int = Field(description='已使用额度')
    remain_quota: int = Field(description='剩余额度')
    request_count: int = Field(description='请求次数')


class NewApiUsageSummaryItem(SchemaBase):
    """用量统计项（按模型分组）"""
    model_name: str = Field(description='模型名称')
    prompt_tokens: int = Field(description='输入 tokens')
    completion_tokens: int = Field(description='输出 tokens')
    quota: int = Field(description='消耗额度')
    request_count: int = Field(description='请求次数')


class NewApiUsageSummary(SchemaBase):
    """用量统计概览"""
    items: list[NewApiUsageSummaryItem] = Field(description='按模型分组的用量')
    total_prompt_tokens: int = Field(description='总输入 tokens')
    total_completion_tokens: int = Field(description='总输出 tokens')
    total_quota: int = Field(description='总消耗额度')
    total_requests: int = Field(description='总请求次数')
    period_start: int = Field(description='统计开始时间 (unix)')
    period_end: int = Field(description='统计结束时间 (unix)')


class NewApiUsageDetailItem(SchemaBase):
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


class NewApiUsageDetail(SchemaBase):
    """用量明细（分页）"""
    items: list[NewApiUsageDetailItem] = Field(description='明细列表')
    total: int = Field(description='总记录数')


class NewApiMappingInfo(SchemaBase):
    """映射信息（登录时返回）"""
    huanxing_user_id: int
    newapi_user_id: int
    newapi_token_key: str = Field(description='new-api API Key')
    app_code: str
    status: str
