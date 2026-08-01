"""`cloud_node` 用量探针——把「主人当前跑着几个云端节点」告诉通用 feature_plan 退款回收。

`cloud:node` 的发货/退款走 `billing/service/feature_plan_callback.py` 的**通用**处理器
（它只认商品目录数据，不认识云端节点）。但「退款后配额低于在跑节点数」这个缺口只有托管域算得出来，
所以用量口径留在本模块，经 `register_feature_usage_probe` 注册给通用侧回调。

探针**只用于退款后的如实告警**，不参与准入判定——判定的唯一入口仍是
`cloud_node_service._assert_can_create`（附赠 ∪ 加购）。停不停机是运营与生命周期 sweep 的决策，
本探针不代为处置。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from backend.app.hasn_hosting.constants import CLOUD_NODE_FEATURE_KEY, NODE_STATUS_DELETED
from backend.app.hasn_hosting.model import HasnCloudNodes
from backend.common.log import log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def cloud_node_usage(db: AsyncSession, *, owner_hasn_id: str) -> dict[str, int]:
    """该主人当前占用的云端节点数，键名与配额快照的 `max_cloud_nodes` 对齐。

    口径与 `cloud_node_service._assert_can_create` 的 `used` 完全一致（已删除的不计），
    否则告警里的「缺口」和真正的配额闸对不上号。
    """
    used = int(
        (
            await db.execute(
                select(func.count())
                .select_from(HasnCloudNodes)
                .where(
                    HasnCloudNodes.owner_hasn_id == owner_hasn_id,
                    HasnCloudNodes.status != NODE_STATUS_DELETED,
                )
            )
        ).scalar()
        or 0
    )
    return {'max_cloud_nodes': used}


def register_cloud_node_usage_probe() -> None:
    """注册 `cloud_node` 用量探针 — 在应用启动时调用（registrar）。"""
    from backend.app.billing.service.feature_plan_callback import register_feature_usage_probe

    register_feature_usage_probe(CLOUD_NODE_FEATURE_KEY, cloud_node_usage)
    log.info('[hosting] 已注册 cloud_node 用量探针（退款后配额缺口告警用）')
