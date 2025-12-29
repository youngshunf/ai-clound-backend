"""手机号认证 Schema
@author Ysf
"""

from datetime import datetime

from pydantic import Field

from backend.common.schema import CustomPhoneNumber, SchemaBase


class SendCodeParam(SchemaBase):
    """发送验证码参数"""

    phone: CustomPhoneNumber = Field(description='手机号')


class SendCodeResponse(SchemaBase):
    """发送验证码响应"""

    success: bool = Field(description='是否成功')
    message: str = Field(default='验证码已发送', description='消息')


class PhoneLoginParam(SchemaBase):
    """手机号登录参数"""

    phone: CustomPhoneNumber = Field(description='手机号')
    code: str = Field(min_length=4, max_length=6, description='验证码')


class PhoneLoginResponse(SchemaBase):
    """手机号登录响应"""

    access_token: str = Field(description='访问令牌')
    access_token_expire_time: datetime = Field(description='访问令牌过期时间')
    refresh_token: str = Field(description='刷新令牌')
    refresh_token_expire_time: datetime = Field(description='刷新令牌过期时间')
    llm_token: str = Field(description='LLM API Token')
    is_new_user: bool = Field(description='是否新用户')
    user: 'PhoneLoginUserInfo' = Field(description='用户信息')


class PhoneLoginUserInfo(SchemaBase):
    """手机号登录用户信息"""

    id: int = Field(description='用户 ID')
    uuid: str = Field(description='用户 UUID')
    username: str = Field(description='用户名')
    nickname: str = Field(description='昵称')
    phone: str | None = Field(default=None, description='手机号')
    email: str | None = Field(default=None, description='邮箱')
    avatar: str | None = Field(default=None, description='头像')
    is_new_user: bool = Field(default=False, description='是否新用户')


class GetLLMTokenResponse(SchemaBase):
    """获取 LLM Token 响应"""

    api_token: str = Field(description='LLM API Token')
    expires_at: datetime | None = Field(default=None, description='过期时间')
