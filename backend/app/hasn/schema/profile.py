"""HASN 用户端合并 profile schema.

合并 `sys_user`（业务/账号资料）与 `hasn_humans`（HASN 身份）两表的字段。
hasn-node daemon 通过 `GET/PUT /api/v1/hasn/app/profile/me` 一次拿到 / 写回
所有可编辑字段，避免对端分别调两表。
"""
from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class GetMergedProfile(SchemaBase):
    """合并 profile 详情（GET 返回值）。"""

    model_config = ConfigDict(from_attributes=True)

    # 关联键 — hasn-node daemon 本地 owners 通过 user_id 跟 sys_user /
    # hasn_humans 对齐；缺这个字段则 daemon 无法把 cloud 数据反向定位
    # 到自己的 OwnerScope。
    user_id: int = Field(description='sys_user.id (BIGINT)')

    # hasn_humans 身份字段（只读）
    hasn_id: str = Field(description='HASN 唯一标识 (h_{uuid})')
    star_id: str = Field(description='唤星号 (注册后不可改)')

    # 公开身份（双写 sys_user + hasn_humans）
    nickname: str = Field(description='昵称')
    avatar: str | None = Field(None, description='头像 URL')
    bio: str | None = Field(None, description='个人简介')

    # sys_user 业务字段
    gender: str | None = Field(None, description='性别 (male/female/other)')
    birthday: str | None = Field(None, description='生日 (YYYY-MM-DD)')
    province: str | None = Field(None, description='省份')
    city: str | None = Field(None, description='城市')
    district: str | None = Field(None, description='区')
    phone: str | None = Field(None, description='手机号 (只读 — 改动走 captcha 流)')
    email: str | None = Field(None, description='邮箱 (只读 — 改动走 captcha 流)')

    # hasn_humans 扩展字段
    timezone: str | None = Field(None, description='时区')

    created_at: datetime | None = Field(None, description='创建时间')
    updated_at: datetime | None = Field(None, description='更新时间')

    @property
    def profile_complete(self) -> bool:
        """计算资料完整性（昵称已设置且非默认值）"""
        if not self.nickname or self.nickname.strip() == '':
            return False
        # 检查是否为默认昵称格式
        # 1. "用户12345" 格式
        if self.nickname.startswith('用户') and self.nickname[2:].isdigit():
            return False
        # 2. 带星号的手机号格式（如 "138****5678"）
        if '****' in self.nickname or '***' in self.nickname:
            return False
        # 3. 纯数字（可能是手机号）
        return not (self.nickname.isdigit() and len(self.nickname) >= 10)


class UpdateMergedProfileParam(SchemaBase):
    """合并 profile 更新参数（PUT 请求体）。

    每个字段都是 Optional 的；未提供的字段不会被写入云端。phone / email /
    star_id / hasn_id 不在这里 — 三者要么只读，要么需要单独的验证码流。
    """

    nickname: str | None = Field(None, description='昵称')
    avatar: str | None = Field(None, description='头像 URL')
    bio: str | None = Field(None, description='个人简介')
    gender: str | None = Field(None, description='性别 (male/female/other)')
    birthday: str | None = Field(None, description='生日 (YYYY-MM-DD)')
    province: str | None = Field(None, description='省份')
    city: str | None = Field(None, description='城市')
    district: str | None = Field(None, description='区')
    timezone: str | None = Field(None, description='时区')
