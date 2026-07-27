"""统一通知服务模型。

- 通知权威行 HasnNotifications 仍定义在 app/hasn（超集 additive，§4.1），此处 re-export 复用。
- 偏好行 HasnNotificationPreferences 为本模块新表（§4.4）。
"""
from backend.app.hasn.model.hasn_notifications import HasnNotifications as HasnNotifications
from backend.app.notification.model.hasn_notification_preferences import (
    HasnNotificationPreferences as HasnNotificationPreferences,
)
from backend.app.notification.model.hasn_service_accounts import (
    HasnServiceAccounts as HasnServiceAccounts,
)
from backend.app.notification.model.hasn_notification_im_command_outbox import HasnNotificationImCommandOutbox as HasnNotificationImCommandOutbox
