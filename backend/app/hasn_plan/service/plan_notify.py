"""规划模块跨路径通知辅助（PLAN-ENT [04] §6.2/§6.3）。

会议邀请卡片通知需要在**两条路径**都发出——分身工具面（`mcp/tools/plan.py` 的
`event.invite`）与主人 WebUI 路径（`api/v1/app/plan.py` 的 owner invite）。为避免两处各写一份
（漂移风险），统一收敛到本模块的 `notify_invited`，两侧共用同一实现。

best-effort：单个通知失败逐人隔离、绝不阻断主流程（写 attendee 已成功）。
"""

import logging

from typing import Any

logger = logging.getLogger(__name__)


async def notify_invited(db: Any, *, event_id: int, added: list[str], organizer_name: str) -> None:
    """给新加入的参会人发会议邀请卡片（深链会议详情，复用 notifications.emit）；best-effort，绝不阻断。

    - ``added``：本次新增的参会人 hasn_id 列表（可跨主人：同企业同事）；空则直接返回。
    - ``organizer_name``：组织者展示名（分身路径=分身名，主人路径=主人昵称）。
    - db 会话非并发安全 → 顺序发；单人失败仅告警不抛。
    """
    if not added:
        return
    from backend.app.notification.service.notification_service import notification_service

    for hid in added:
        try:
            await notification_service.app_emit(
                db,
                app_id='plan',
                owner_hasn_id=hid,  # recipient = 被邀参会人（可跨主人：同企业同事）
                category='app',
                type='plan.event.invited',
                title='会议邀请',
                body=f'{organizer_name} 邀请你参加一个会议',
                payload={'kind': 'plan_event', 'event_id': event_id, 'uri': f'hasn://plan/event/{event_id}'},
                priority='normal',
                want_card=True,
            )
        except Exception as e:  # noqa: PERF203 — 逐人隔离：单个通知失败不阻断其余（db 会话非并发安全，故顺序发）
            logger.warning('[plan] event.invite 通知被邀人 %s 失败 (非致命): %s', hid, e)
