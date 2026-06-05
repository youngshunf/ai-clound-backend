"""统一通知服务 schema（§4.2 / §4.3 / §4.4 / §5）。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from backend.common.schema import SchemaBase

# 发送主体类型，对齐云端既有 CardSourceKind（hasn_card_message.py）
NotificationSourceKind = Literal['app', 'agent', 'system', 'user', 'external']

# 通知粗类（定默认投递策略与默认优先级，§4.3）
NotificationCategory = Literal[
    'social', 'contact', 'message', 'agent', 'app', 'commerce', 'system', 'reminder'
]

NotificationPriority = Literal['critical', 'high', 'normal', 'low']

# 投递渠道
NotificationChannel = Literal['center', 'card_message', 'toast', 'push', 'badge']


class NotificationSource(SchemaBase):
    """统一"谁发的"（§4.2）。"""

    kind: NotificationSourceKind
    id: str | None = None
    display_name: str = ''
    avatar: str | None = None
    on_behalf_of: str | None = None  # App/Agent 代表的主人 hasn_id，可空


class NotificationChannels(SchemaBase):
    """渠道开关（§4.4，center 不可关）。"""

    center: bool = True
    card_message: bool = False
    toast: bool = False
    push: bool = False
    badge: bool = True


class NotificationDnd(SchemaBase):
    """免打扰窗口（§4.4）。"""

    enabled: bool = False
    start: str = '22:00'
    end: str = '08:00'
    tz: str | None = None
    allow_critical: bool = True


class PreferenceUpdate(SchemaBase):
    """主人偏好更新（按 category，或 '*' 全局默认）。"""

    category: str = Field(default='*', description="通知粗类，或 '*' 表全局默认")
    channels: NotificationChannels | None = None
    dnd: NotificationDnd | None = None


class EmitRequest(SchemaBase):
    """Agent/App 发通知请求体（身份取自 JWT，源信息在服务端补全 §7）。"""

    recipient_id: str = Field(..., description='接收方 hasn_id（主人或 Agent）')
    category: NotificationCategory = Field('app', description='通知粗类')
    type: str = Field(..., description='通知细类（自定义字符串）')
    title: str = Field(..., min_length=1, max_length=200, description='通知标题')
    body: str | None = Field(None, description='通知正文')
    payload: dict[str, Any] = Field(default_factory=dict, description='{actor,target,preview,link,actions}')
    priority: NotificationPriority = Field('normal', description='优先级')
    dedupe_key: str | None = Field(None, description='去重键')
    group_key: str | None = Field(None, description='聚合键')
    delivery_hint: dict[str, Any] | None = Field(None, description='来源建议承载（受偏好与权限收敛）')


class AppEmitRequest(SchemaBase):
    """AI-Native App 发通知请求体（§7 / P5）。

    身份取自 Agent JWT，source 由服务端按 manifest 补全（kind=app, id=app_id,
    on_behalf_of=owner）。recipient 恒为本 Agent 的主人（MVP 边界，不在请求体出现）。
    category 须在该 App manifest 的 `notifications.emit` 白名单内；card 受 manifest
    `card_message` 开关 + 主人偏好双重收敛。
    """

    app_id: str = Field(..., min_length=1, description='发通知的 App id（须已发布且声明 notifications.emit）')
    category: NotificationCategory = Field('app', description='通知粗类（须在 App 白名单内）')
    type: str = Field(..., description='通知细类（自定义字符串）')
    title: str = Field(..., min_length=1, max_length=200, description='通知标题')
    body: str | None = Field(None, description='通知正文')
    payload: dict[str, Any] = Field(default_factory=dict, description='{actor,target,preview,link,actions}')
    priority: NotificationPriority = Field('normal', description='优先级')
    card: bool = Field(True, description='是否请求卡片消息承载（受 manifest card_message + 主人偏好收敛）')
    dedupe_key: str | None = Field(None, description='去重键')
    group_key: str | None = Field(None, description='聚合键')
