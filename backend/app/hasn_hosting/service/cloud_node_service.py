"""云端托管节点编排服务（H4 · 契约 §3.1 / §3.3）。

云端在这条链路上的角色是**控制面**：判订阅与配额、铸授权码、经 provider 中转 hosting-agent、
把宿主回报的容器事实镜像成托管状态。它**不碰 Docker**，也**不以容器状态判在线**。

三条不可动摇的规矩：

1. **授权码绝不回给客户端**——明文只在 `create/reauthorize` 的进程内从铸造点直达 provider。
2. **在线判定只认 Redis presence**——容器 running 但 presence 未命中 = `failed(daemon_not_online)`。
   进程活着不等于分身在线，报在线就是造假（D-13 零 fake）。
3. **删除先吊销凭据再销毁容器**——顺序反了会出现「容器没了但设备凭据还能用」的窗口。
"""

from __future__ import annotations

import uuid

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import desc, func, select

from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.billing.service.access_service import resolve_access
from backend.app.hasn.model.hasn_nodes import HasnNodes
from backend.app.hasn.service.hasn_node_bindings_service import hasn_node_bindings_service
from backend.app.hasn_hosting.constants import (
    CLOUD_NODE_FEATURE_KEY,
    CODE_PURPOSE_CREATE,
    CODE_PURPOSE_REAUTHORIZE,
    ERR_NODE_MEMORY_CEILING,
    ERR_QUOTA_EXCEEDED,
    ERR_REAUTHORIZE_NOT_ALLOWED,
    ERR_SUBSCRIPTION_REQUIRED,
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_FAILED,
    EVENT_REAUTHORIZED,
    EVENT_RESIZED,
    EVENT_STARTED,
    EVENT_STOPPED,
    EVENT_TYPES,
    FAILURE_CREDENTIAL_INVALID,
    FAILURE_DAEMON_NOT_ONLINE,
    FAILURE_INTERNAL_ERROR,
    FAILURE_REASONS,
    IMAGE_ASSET_KIND,
    LLM_TIER_OFFERING_KEY,
    NODE_STATUSES,
    NODE_STATUS_DELETED,
    NODE_STATUS_DELETING,
    NODE_STATUS_FAILED,
    NODE_STATUS_ONLINE,
    NODE_STATUS_PROVISIONING,
    NODE_STATUS_STARTING,
    NODE_STATUS_STOPPED,
    NODE_STATUS_UPDATING,
    TIER_GRANT_FALLBACK,
)
from backend.app.hasn_hosting.model import HasnCloudNodeEvents, HasnCloudNodes
from backend.app.hasn_hosting.provider.agent_client import HostingAgentError, hosting_agent_provider
from backend.app.hasn_hosting.service.access_ticket_service import IssuedTicket, access_ticket_service
from backend.app.hasn_hosting.service.authorization_code_service import authorization_code_service
from backend.app.hasn_im.application.provider import get_node_session_gateway, get_presence_query
from backend.app.hasn_release.model import AppRelease, ReleaseAsset
from backend.common.exception import errors
from backend.common.log import log
from backend.common.security.jwt import revoke_token
from backend.core.conf import settings
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_presence_query = get_presence_query()
_node_session_gateway = get_node_session_gateway()

#: 详情页返回的最近事件条数
_RECENT_EVENT_LIMIT = 20


def _new_cloud_node_id() -> str:
    """云端节点的 node_id：`n_cloud_` + 16 位 hex（`hasn_nodes.node_id` 上限 40 字符）。"""
    return f'n_cloud_{uuid.uuid4().hex[:16]}'


#: 「持有 cloud_node 加购权益」的判定原因码（`resolve_access` 十态里真的有权益的那几个）。
#: 特意**不含** `free`——那是「库里没配 cloud:node 商品」时的非门控放行，
#: 把它当成持有权益会让所有人（含免费档）凭空获得加购资格，付费墙直接失效。
_ADDON_HELD_REASONS: tuple[str, ...] = ('entitled', 'trialing', 'expired_in_grace')


class CloudNodeService:
    """Owner 面 + 内部面共用的托管编排。"""

    # ─── 订阅与配额（混合结构，主人 2026-08-01 拍板） ───
    #
    # 云端节点的名额有**两个来源**：
    #   来源 A｜LLM 订阅档附赠：五档 free/lite/pro/max/ultra 中**只有 pro 及以上各附赠 1 个**，
    #           free 与 lite 都是 0（不给试用）；未定档的档位一律按 0（`_tier_grant` fail-closed）；
    #   来源 B｜`cloud:node` 独立商品加购：¥99/月 · ¥950/年（与 `pro` 同价同折扣），买几份加几个。
    #
    # 两条来源必须**取并集判准入、求和算上限**：
    #   允许创建 ⟺ （档位附赠数 > 0） 或 （持有有效 cloud_node 权益）
    #   配额上限  = 档位附赠数 ＋ 权益快照里的加购数
    #
    # 少任何一路都是事故：只认权益 → `cloud_node` 一进 `FIXED_FEATURE_KEYS`，
    # `resolve_access` 就从「未知特征放行」翻成「无权益即拒」，pro 用户的附赠被自家付费墙挡死；
    # 只认附赠 → 加购的钱白花。`is_entitled`（sweep 判存活）与本口径**逐条对齐**，
    # 否则附赠用户会被判成「订阅失效」停机，30 天后连数据卷一起销毁。

    @staticmethod
    async def _active_subscription(db: AsyncSession, *, user_id: int) -> UserSubscription | None:
        """取该用户当前有效订阅（status=active 且未过期）。"""
        rows = (
            await db.execute(
                select(UserSubscription)
                .where(UserSubscription.user_id == user_id, UserSubscription.status == 'active')
                .order_by(desc(UserSubscription.id))
            )
        ).scalars().all()
        now = timezone.now()
        for row in rows:
            end = row.subscription_end_date or row.contract_end_at
            if end is None or end > now:
                return row
        return None

    @staticmethod
    async def _tier_quota(db: AsyncSession, subscription: UserSubscription, key: str) -> int:
        """从订阅档位读一个配额键，取值顺序固定（先命中先返回）：

        1. 合同快照 `plan_snapshot.<key>`——购买时固化的合同参数，权威且不随改价漂移；
        2. `billing_plan.quota_json`，按合同上的 `(offering_key, plan_key)` 精确命中；
        3. `billing_plan.quota_json`，按 `tier` 名反查 `llm:tier` 档位——存量合同（尤其
           `credit_grant_service` 建的 free 档）根本不写 `offering_key`/`plan_key`，没有这一步
           它们会一路掉到兜底值；月付/年付同档配额相同，取任一即可；
        4. **兜底 fail-closed 为 0**，并 `warn` 记录是哪个档位没定档。

        第 4 条是本方法的立场：**送资源必须是显式定档的结果，没定档就是 0**，并且要在日志里
        看得见，而不是静默送。`max_cloud_nodes` 曾回落配置值 1，主人 2026-08-01 五档定档后
        改判——新档位若上线时漏写，回落 1 就等于免费级别的档白得一个常驻容器（真金白银的算力）。
        `max_node_memory_mb`（H9-b）沿用同一取向：没定档就调不上去，不白送内存。

        事实源：`docs/hasn-node设计文档/16-订阅与积分计费/05-订阅档位与权益定档（五档命名·额度·门禁口径）.md` §2.1
        """
        snapshot = subscription.plan_snapshot or {}
        raw = snapshot.get(key)
        if raw is None and subscription.offering_key and subscription.plan_key:
            plan = (
                await db.execute(
                    select(BillingPlan).where(
                        BillingPlan.offering_key == subscription.offering_key,
                        BillingPlan.plan_key == subscription.plan_key,
                    )
                )
            ).scalar_one_or_none()
            if plan is not None:
                raw = (plan.quota_json or {}).get(key)
        if raw is None and subscription.tier:
            plan = (
                await db.execute(
                    select(BillingPlan)
                    .where(
                        BillingPlan.offering_key == LLM_TIER_OFFERING_KEY,
                        BillingPlan.quota_json['tier'].astext == subscription.tier,
                        BillingPlan.status == 'active',
                    )
                    .order_by(BillingPlan.sort_order, BillingPlan.plan_key)
                )
            ).scalars().first()
            if plan is not None:
                raw = (plan.quota_json or {}).get(key)
        if raw is None:
            log.warning(
                f'[hosting] 档位未定档 {key}，按 0 计（不静默送资源）: '
                f'订阅 {subscription.id}, tier={subscription.tier!r}, '
                f'offering={subscription.offering_key!r}, plan={subscription.plan_key!r}'
            )
            return TIER_GRANT_FALLBACK
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            log.warning(f'[hosting] 订阅 {subscription.id} 的 {key} 非法（{raw!r}），按 0 计（不静默送资源）')
            return TIER_GRANT_FALLBACK

    @classmethod
    async def _tier_grant(cls, db: AsyncSession, subscription: UserSubscription) -> int:
        """**来源 A**：该订阅所在 LLM 档位**附赠**的云端节点数。"""
        return await cls._tier_quota(db, subscription, 'max_cloud_nodes')

    @classmethod
    async def _tier_memory_ceiling(cls, db: AsyncSession, subscription: UserSubscription) -> int:
        """该订阅档位允许的**单节点内存上限**（MiB，H9-b）。

        与 `_tier_grant` 语义不同：那个是**数量**（档位附赠 ＋ 加购**求和**），这个是**每个
        节点多大**的天花板（档位与加购之间取 **max**）。买两份加购不该让单个节点变成两倍大
        ——那是买了两个节点，不是买了一个双倍大的节点。
        """
        return await cls._tier_quota(db, subscription, 'max_node_memory_mb')

    @staticmethod
    async def _addon_access(db: AsyncSession, *, owner_hasn_id: str) -> tuple[bool, int]:
        """**来源 B**：`cloud:node` 加购权益 → `(是否持有有效权益, 加购节点数)`。

        走商业化内核统一判定入口，宽限期 / 下架 / 超额等口径全部复用，不在托管侧另立一套。
        只有 `_ADDON_HELD_REASONS` 里的原因码才算「真的持有权益」——`reason='free'` 表示库里
        压根没配 `cloud:node` 商品（非门控放行），此时加购数为 0 且**不**视为持有。
        """
        decision = await resolve_access(
            db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_type='owner', subject_id=owner_hasn_id
        )
        if not decision.allowed or decision.reason not in _ADDON_HELD_REASONS:
            return False, 0
        snapshot = (decision.quota.snapshot if decision.quota else None) or {}
        raw = snapshot.get('max_cloud_nodes')
        if raw is None:
            # 宽限期分支不带配额快照，或权益行漏了该键：持有资格成立，但加不出具体名额。
            return True, 0
        try:
            return True, max(0, int(raw))
        except (TypeError, ValueError):
            log.warning(f'[hosting] 主人 {owner_hasn_id} 的 cloud_node 权益 max_cloud_nodes 非法（{raw!r}），按 0 计')
            return True, 0

    async def _quota_of(
        self, db: AsyncSession, subscription: UserSubscription | None, *, owner_hasn_id: str
    ) -> int:
        """该主人的云端节点数上限 = 档位附赠数 ＋ 加购数（混合结构求和）。"""
        grant = await self._tier_grant(db, subscription) if subscription is not None else 0
        _, addon = await self._addon_access(db, owner_hasn_id=owner_hasn_id)
        return grant + addon

    @staticmethod
    async def _addon_memory_ceiling(db: AsyncSession, *, owner_hasn_id: str) -> int:
        """`cloud:node` 加购权益允许的单节点内存上限（MiB，H9-b）。

        没有权益、权益不带这个键、或者值非法 → 一律 0（fail closed，不静默送内存），
        与 `_tier_quota` 的兜底取向一致。
        """
        decision = await resolve_access(
            db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_type='owner', subject_id=owner_hasn_id
        )
        if not decision.allowed or decision.reason not in _ADDON_HELD_REASONS:
            return 0
        snapshot = (decision.quota.snapshot if decision.quota else None) or {}
        raw = snapshot.get('max_node_memory_mb')
        if raw is None:
            return 0
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            log.warning(
                f'[hosting] 主人 {owner_hasn_id} 的 cloud_node 权益 max_node_memory_mb 非法（{raw!r}），按 0 计'
            )
            return 0

    async def _memory_ceiling_of(self, db: AsyncSession, *, user_id: int, owner_hasn_id: str) -> int:
        """该主人的**单节点内存上限**（MiB）= max(档位天花板, 加购天花板)。

        **取 max 不是求和**：天花板是「每个节点能有多大」，买两份加购是买了两个节点，
        不是把一个节点变成两倍大。这一条与 `_quota_of`（数量，求和）的差别必须记牢，
        写成求和会让加购用户拿到远超定价意图的单机规格。
        """
        subscription = await self._active_subscription(db, user_id=user_id)
        tier = await self._tier_memory_ceiling(db, subscription) if subscription is not None else 0
        addon = await self._addon_memory_ceiling(db, owner_hasn_id=owner_hasn_id)
        return max(tier, addon)

    async def _assert_can_create(self, db: AsyncSession, *, user_id: int, owner_hasn_id: str) -> None:
        """创建前置闸：附赠/加购并集准入 + 未超配额（求和上限）。任一不过即抛带错误码的 4xx。"""
        subscription = await self._active_subscription(db, user_id=user_id)
        grant = await self._tier_grant(db, subscription) if subscription is not None else 0
        addon_held, addon = await self._addon_access(db, owner_hasn_id=owner_hasn_id)

        # 准入闸：两个来源取并集。free 档（附赠 0）且无加购权益 → 明确拒绝，
        # 错误码保持 ERR_SUBSCRIPTION_REQUIRED（WebUI 空态文案「云端节点随订阅开通」按它分支）。
        if grant <= 0 and not addon_held:
            log.warning(
                f'[hosting] 创建云端节点被拒（无档位附赠且无加购权益）: '
                f'user_id={user_id}, owner={owner_hasn_id}, tier_grant={grant}'
            )
            raise errors.ForbiddenError(
                msg='需要有效订阅或加购云端节点才能创建', data={'error': ERR_SUBSCRIPTION_REQUIRED}
            )

        quota = grant + addon
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
        if used >= quota:
            log.warning(
                f'[hosting] 云端节点配额超限: owner={owner_hasn_id}, used={used}, '
                f'quota={quota}（附赠 {grant} + 加购 {addon}）'
            )
            raise errors.ForbiddenError(
                msg=f'已达云端节点配额上限（{used}/{quota}）',
                data={'error': ERR_QUOTA_EXCEEDED, 'used': used, 'quota': quota},
            )

    async def is_entitled(self, db: AsyncSession, *, user_id: int, owner_hasn_id: str) -> bool:
        """该主人当前是否仍有资格持有云端节点（生命周期 sweep 判「订阅到期」/「续订恢复」用）。

        判据与 `_assert_can_create` 的**准入闸逐条对齐**——附赠与加购取并集：

        - 有未过期的 active 订阅，且其档位附赠数 > 0（pro 及以上）；**或**
        - 持有有效的 `cloud_node` 加购权益。

        两条缺一不可对齐：漏了附赠这一路，pro 用户零加购的节点会被 sweep 判成「订阅失效」
        而停机，30 天后连数据卷一起销毁——这是本模块最贵的一种误判。

        配额闸刻意不参与：存量节点不该因为档位下调而被停机销毁，那属于运营决策，不是「订阅到期」。
        本方法不抛错、只回布尔：sweep 是批处理，单个主人判定失败不该炸掉整轮。
        """
        subscription = await self._active_subscription(db, user_id=user_id)
        if subscription is not None and await self._tier_grant(db, subscription) > 0:
            return True
        addon_held, _ = await self._addon_access(db, owner_hasn_id=owner_hasn_id)
        return addon_held

    async def active_subscription_end(self, db: AsyncSession, *, user_id: int) -> datetime | None:
        """当前有效订阅的到期时刻（到期前提醒用）。

        免费版 `subscription_end_date` 为 NULL = 永不过期，返回 None 表示「不参与到期提醒」，
        绝不臆造一个到期日去吓唬用户。
        """
        subscription = await self._active_subscription(db, user_id=user_id)
        if subscription is None:
            return None
        return subscription.subscription_end_date or subscription.contract_end_at

    # ─── 镜像解析（对接 hasn_release，H8） ───

    @staticmethod
    async def resolve_image(
        db: AsyncSession,
        *,
        platform_target: str | None = None,
        channel: str = 'stable',
    ) -> tuple[str, str, str]:
        """解析当前应发布的无头镜像 `(image_ref, image_digest, version)`。

        以 `hasn_release` 为唯一事实源：`app_release` 中 **stable 渠道**已发布版本里，`release_asset`
        中 `platform_target=headless-linux-*` 且 `asset_kind='image'` 的那条；`download_url` 是
        registry ref，`sha256` 是镜像 digest（以 digest 为准，不信 tag）。查不到即抛，
        **绝不回落一个猜出来的 tag**（拉错镜像比不拉更糟）。
        """
        target = platform_target or settings.HOSTING_NODE_IMAGE_PLATFORM
        row = (
            await db.execute(
                select(ReleaseAsset, AppRelease)
                .join(AppRelease, AppRelease.id == ReleaseAsset.release_id)
                .where(
                    ReleaseAsset.platform_target == target,
                    ReleaseAsset.asset_kind == IMAGE_ASSET_KIND,
                    AppRelease.status == 'published',
                    AppRelease.channel == channel,
                )
                .order_by(desc(AppRelease.is_latest), desc(AppRelease.published_time), desc(AppRelease.id))
                .limit(1)
            )
        ).first()
        if row is None:
            raise errors.NotFoundError(msg=f'尚无已发布的无头镜像（platform_target={target}）')
        asset, release = row
        digest = (asset.sha256 or '').strip()
        if not digest:
            # 没有 digest 就无法保证拉到的是同一个镜像 → 不变量破坏，拒绝创建
            log.error(f'[hosting] 无头镜像资产缺 digest: release={release.version}, target={target}')
            raise errors.ServerError(msg='无头镜像资产缺少 digest，拒绝创建容器')
        return asset.download_url, digest, release.version

    # ─── 事件 ───

    @staticmethod
    async def record_event(
        db: AsyncSession,
        *,
        cloud_node: HasnCloudNodes,
        event_type: str,
        detail: dict[str, Any] | None = None,
    ) -> HasnCloudNodeEvents:
        """写一条托管事件（只追加）。"""
        event = HasnCloudNodeEvents(
            cloud_node_id=cloud_node.id,
            node_id=cloud_node.node_id,
            event_type=event_type,
            detail=detail or {},
        )
        db.add(event)
        await db.flush()
        return event

    # ─── 查询 ───

    @staticmethod
    async def _get_owned(db: AsyncSession, *, owner_hasn_id: str, node_id: str) -> HasnCloudNodes:
        row = (
            await db.execute(
                select(HasnCloudNodes).where(
                    HasnCloudNodes.node_id == node_id,
                    HasnCloudNodes.owner_hasn_id == owner_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='云端节点不存在')
        return row

    async def _to_view(
        self,
        row: HasnCloudNodes,
        *,
        node_name: str | None = None,
        latest_digest: str | None = None,
        latest_version: str | None = None,
        backup_configured: bool | None = None,
    ) -> dict[str, Any]:
        """把托管行渲染成对外视图（契约 §3.1.1）；`status` 的在线态**只**由 presence 决定。

        **不透出** `container_ref` / `credential_session_uuid`——运维内部信息，主人面不下发。
        """
        presence_online = await _presence_query.is_node_online(row.node_id)
        status = row.status
        failure_reason = row.failure_reason
        failure_detail = row.failure_detail
        if presence_online:
            status = NODE_STATUS_ONLINE
            failure_reason = None
            failure_detail = None
        elif row.status == NODE_STATUS_ONLINE:
            # 库里记着 online 但 presence 没命中：容器活着不代表 daemon 上线 → 如实报异常
            status = NODE_STATUS_FAILED
            failure_reason = FAILURE_DAEMON_NOT_ONLINE
            failure_detail = failure_detail or 'daemon 未在云端注册 presence'
        return {
            'node_id': row.node_id,
            'node_name': node_name,
            'host': row.host,
            'status': status,
            'failure_reason': failure_reason,
            'failure_detail': failure_detail,
            'presence_online': presence_online,
            'image_version': row.image_version,
            'image_digest': row.image_digest,
            'update_available': bool(
                latest_digest and row.image_digest and latest_digest != row.image_digest
            ),
            'latest_image_version': latest_version,
            # 宿主未配置备份时必须如实为 false（UI 显示「此节点数据无备份」）；
            # 宿主探活不通时为 null（我们确实不知道，不许猜一个 true 掩盖裸奔）
            'backup_configured': backup_configured,
            'retain_until': row.retain_until.isoformat() if row.retain_until else None,
            # NULL 表示尚无备份，UI 必须如实显示，不许当成 0 或隐藏
            'last_backup_at': row.last_backup_at.isoformat() if row.last_backup_at else None,
            'online_since': row.online_since.isoformat() if row.online_since else None,
            # 资源档位（H9-b）：0 表示**尚未从宿主回报过**，不是「限 0 字节」；
            # UI 遇到 0 应显示「未知」而不是「0 MiB」。
            'memory_mb': row.memory_mb,
            'cpus': row.cpus,
            # NULL 表示**测不出来**（宿主看不到卷路径），不是 0；0 会被读成「没占空间」。
            'disk_used_mb': row.disk_used_mb,
            # 数据卷当前**没有任何硬配额**，如实告知，别让 UI 编一个「已用 x/y」的进度条出来。
            'disk_quota_active': False,
            'created_time': row.created_time.isoformat() if row.created_time else None,
            'updated_time': row.updated_time.isoformat() if row.updated_time else None,
        }

    async def _latest_image(self, db: AsyncSession) -> tuple[str | None, str | None]:
        """当前最新无头镜像 `(digest, version)`；未发布时诚实返回 `(None, None)`。"""
        try:
            _ref, digest, version = await self.resolve_image(db)
        except (errors.NotFoundError, errors.ServerError):
            return None, None
        return digest, version

    @staticmethod
    async def _backup_configured() -> bool | None:
        """透传 hosting-agent `/health` 的 `backup_configured`；探活不通时返回 None（未知）。"""
        health = await hosting_agent_provider.health()
        if not health.get('ok'):
            return None
        value = health.get('backup_configured')
        return bool(value) if isinstance(value, bool) else None

    @staticmethod
    async def _node_names(db: AsyncSession, node_ids: list[str]) -> dict[str, str | None]:
        """批量取 `hasn_nodes.node_name`（设备显示名的权威在设备表，不在托管表）。"""
        if not node_ids:
            return {}
        rows = (
            await db.execute(
                select(HasnNodes.node_id, HasnNodes.node_name).where(HasnNodes.node_id.in_(node_ids))
            )
        ).all()
        return {node_id: node_name for node_id, node_name in rows}

    async def _render_one(
        self,
        db: AsyncSession,
        row: HasnCloudNodes,
        *,
        with_agent_view: bool = False,
    ) -> dict[str, Any]:
        """单节点视图（创建/起停/重授权等写端点返回用）。

        `with_agent_view=True` 时额外拉 agent 的 `GET /v1/nodes/{node_id}` 并**原样透传**
        `daemon_endpoint` / `backup_configured` / `last_backup_at` / `egress_guard_active`
        （契约 §4.5⑤）。宿主不可达时这四项留 None（未知），绝不用「看起来正常」的默认值顶替。
        """
        names = await self._node_names(db, [row.node_id])
        latest_digest, latest_version = await self._latest_image(db)
        agent_view = await hosting_agent_provider.try_get_node(row.node_id) if with_agent_view else None
        backup_configured = await self._backup_configured()
        if agent_view is not None and isinstance(agent_view.get('backup_configured'), bool):
            # 单节点粒度的事实优于宿主级 /health，覆盖之
            backup_configured = bool(agent_view['backup_configured'])
        view = await self._to_view(
            row,
            node_name=names.get(row.node_id),
            latest_digest=latest_digest,
            latest_version=latest_version,
            backup_configured=backup_configured,
        )
        if with_agent_view:
            view['daemon_endpoint'] = (agent_view or {}).get('daemon_endpoint')
            view['egress_guard_active'] = (agent_view or {}).get('egress_guard_active')
            agent_last_backup = (agent_view or {}).get('last_backup_at')
            if agent_last_backup:
                view['last_backup_at'] = agent_last_backup
        return view

    async def list_nodes(self, db: AsyncSession, *, owner_hasn_id: str) -> list[dict[str, Any]]:
        """列出本 owner 的云端节点（已删除的不再展示）。"""
        rows = (
            await db.execute(
                select(HasnCloudNodes)
                .where(
                    HasnCloudNodes.owner_hasn_id == owner_hasn_id,
                    HasnCloudNodes.status != NODE_STATUS_DELETED,
                )
                .order_by(desc(HasnCloudNodes.created_time))
            )
        ).scalars().all()
        latest_digest, latest_version = await self._latest_image(db)
        names = await self._node_names(db, [r.node_id for r in rows])
        # 宿主 /health 每次请求只探一次，不按节点数放大
        backup_configured = await self._backup_configured() if rows else None
        return [
            await self._to_view(
                row,
                node_name=names.get(row.node_id),
                latest_digest=latest_digest,
                latest_version=latest_version,
                backup_configured=backup_configured,
            )
            for row in rows
        ]

    async def get_node_detail(self, db: AsyncSession, *, owner_hasn_id: str, node_id: str) -> dict[str, Any]:
        """节点详情 + 最近事件（倒序，最多 20 条）。"""
        row = await self._get_owned(db, owner_hasn_id=owner_hasn_id, node_id=node_id)
        view = await self._render_one(db, row, with_agent_view=True)
        events = (
            await db.execute(
                select(HasnCloudNodeEvents)
                .where(HasnCloudNodeEvents.node_id == node_id)
                .order_by(desc(HasnCloudNodeEvents.created_time))
                .limit(_RECENT_EVENT_LIMIT)
            )
        ).scalars().all()
        view['events'] = [
            {
                'event_type': e.event_type,
                'detail': e.detail or {},
                'created_time': e.created_time.isoformat() if e.created_time else None,
            }
            for e in events
        ]
        return view

    # ─── 创建 ───

    async def create_node(self, db: AsyncSession, *, user_id: int, owner_hasn_id: str) -> dict[str, Any]:
        """创建云端节点（§3.1）。

        顺序：判订阅+配额 → 解析镜像 → 预分配 `hasn_nodes(node_type='cloud')` → 铸授权码
        → 调 hosting-agent 建容器 → 落 `provisioning` + `created` 事件。

        hosting-agent 调用失败时**不回滚**预分配行与授权码，而是把托管行落 `failed` + 真实原因：
        节点身份已经分配出去了，静默删掉会让后续排障无据可查（零 fake：失败必须留痕）。
        """
        await self._assert_can_create(db, user_id=user_id, owner_hasn_id=owner_hasn_id)
        image_ref, image_digest, image_version = await self.resolve_image(db)

        node_id = _new_cloud_node_id()
        host = settings.HOSTING_DEFAULT_HOST

        # 预分配 hasn_nodes：node_type='cloud' 的权威在云端，容器上线时不得覆盖（契约 §0.2）
        db.add(
            HasnNodes(
                node_id=node_id,
                user_id=user_id,
                allowed_owner_hasn_ids=[owner_hasn_id],
                node_type='cloud',
                node_name='云端节点',
                device_fingerprint=None,
                device_platform='server',
                app_version=image_version,
                ip_address=None,
                ip_location=None,
                node_info={'hosting': True, 'host': host},
                node_key_hash=None,
                capacity=1,
                created_by_owner_id=owner_hasn_id,
                last_seen_at=None,
                status='active',
            )
        )
        cloud_node = HasnCloudNodes(
            node_id=node_id,
            user_id=user_id,
            owner_hasn_id=owner_hasn_id,
            host=host,
            container_ref=None,
            status=NODE_STATUS_PROVISIONING,
            failure_reason=None,
            failure_detail=None,
            image_version=image_version,
            image_digest=image_digest,
            credential_session_uuid=None,
            retain_until=None,
            last_backup_at=None,
            online_since=None,
        )
        db.add(cloud_node)
        await db.flush()

        minted = await authorization_code_service.mint(
            db, user_id=user_id, owner_hasn_id=owner_hasn_id, node_id=node_id, purpose=CODE_PURPOSE_CREATE
        )
        await self.record_event(
            db,
            cloud_node=cloud_node,
            event_type=EVENT_CREATED,
            detail={'image_version': image_version, 'image_digest': image_digest, 'host': host},
        )

        try:
            result = await hosting_agent_provider.create_node(
                node_id=node_id,
                owner_hasn_id=owner_hasn_id,
                authorization_code=minted.plain_code,
                image_ref=image_ref,
                image_digest=image_digest,
                memory_mb=int(settings.HOSTING_NODE_MEMORY_MB),
                cpus=float(settings.HOSTING_NODE_CPUS),
                backend_http_base=settings.HOSTING_NODE_BACKEND_HTTP_BASE,
                backend_ws_url=settings.HOSTING_NODE_BACKEND_WS_URL,
            )
        except HostingAgentError as exc:
            cloud_node.status = NODE_STATUS_FAILED
            # 原因码只认 agent 显式给的 detail.failure_reason（契约 §4.5⑥），拿不到就落 internal_error；
            # 绝不靠 str(exc) 里有没有 'image' 猜——猜错会把 UI 引到完全不相干的自救路径上。
            cloud_node.failure_reason = exc.failure_reason or FAILURE_INTERNAL_ERROR
            cloud_node.failure_detail = str(exc)
            await self.record_event(
                db,
                cloud_node=cloud_node,
                event_type=EVENT_FAILED,
                detail={
                    'stage': 'create',
                    'error': exc.code,
                    'status_code': exc.status_code,
                    'message': str(exc),
                },
            )
            await db.flush()
            # 容器没建起来 = 该节点当前不可用，是本次请求的永久失败 → error
            log.error(f'[hosting] 建容器失败: node_id={node_id}, err={exc}')
            return await self._render_one(db, cloud_node)

        cloud_node.container_ref = str(result.get('container_ref') or '') or None
        cloud_node.status = NODE_STATUS_STARTING
        await db.flush()
        return await self._render_one(db, cloud_node)

    # ─── 生命周期动作 ───

    async def _agent_action(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        node_id: str,
        action: str,
    ) -> dict[str, Any]:
        """start / stop / restart 中转（§3.1）。"""
        row = await self._get_owned(db, owner_hasn_id=owner_hasn_id, node_id=node_id)
        caller = {
            'start': hosting_agent_provider.start_node,
            'stop': hosting_agent_provider.stop_node,
            'restart': hosting_agent_provider.restart_node,
        }[action]
        try:
            await caller(node_id)
        except HostingAgentError as exc:
            row.failure_reason = exc.failure_reason or FAILURE_INTERNAL_ERROR
            row.failure_detail = str(exc)
            await self.record_event(
                db,
                cloud_node=row,
                event_type=EVENT_FAILED,
                detail={'stage': action, 'error': exc.code, 'status_code': exc.status_code},
            )
            await db.flush()
            log.error(f'[hosting] {action} 失败: node_id={node_id}, err={exc}')
            raise errors.ServerError(msg=f'托管宿主执行 {action} 失败：{exc}')

        if action == 'stop':
            row.status = NODE_STATUS_STOPPED
            row.online_since = None
            await self.record_event(db, cloud_node=row, event_type=EVENT_STOPPED, detail={})
        else:
            row.status = NODE_STATUS_STARTING
            row.failure_reason = None
            row.failure_detail = None
            await self.record_event(db, cloud_node=row, event_type=EVENT_STARTED, detail={'action': action})
        await db.flush()
        return await self._render_one(db, row)

    async def start_node(self, db: AsyncSession, *, owner_hasn_id: str, node_id: str) -> dict[str, Any]:
        return await self._agent_action(db, owner_hasn_id=owner_hasn_id, node_id=node_id, action='start')

    async def stop_node(self, db: AsyncSession, *, owner_hasn_id: str, node_id: str) -> dict[str, Any]:
        return await self._agent_action(db, owner_hasn_id=owner_hasn_id, node_id=node_id, action='stop')

    async def restart_node(self, db: AsyncSession, *, owner_hasn_id: str, node_id: str) -> dict[str, Any]:
        return await self._agent_action(db, owner_hasn_id=owner_hasn_id, node_id=node_id, action='restart')

    async def resize_node(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        owner_hasn_id: str,
        node_id: str,
        memory_mb: int | None = None,
        cpus: float | None = None,
    ) -> dict[str, Any]:
        """改节点资源档位（H9-b）：按订阅档校验天花板 → 中转 hosting-agent → 回写规格镜像。

        触发场景：主人在该节点的 WebUI 装了图坊/语音引擎，内存需求跳一档。容器里的 daemon
        改不了自己的 cgroup 上限，只能由宿主侧重建容器来调。

        两道闸各管各的，**不能合并**：
        - 这里管**订阅档天花板**（这个主人的档位允许单节点多大）；
        - hosting-agent 管**宿主水位**（这台机器此刻还放不放得下）。
          天花板过了但宿主满了照样要拒，那是 `resource_exhausted`，不是「你档位不够」。
        """
        row = await self._get_owned(db, owner_hasn_id=owner_hasn_id, node_id=node_id)

        if memory_mb is not None:
            ceiling = await self._memory_ceiling_of(db, user_id=user_id, owner_hasn_id=owner_hasn_id)
            if memory_mb > ceiling:
                log.warning(
                    f'[hosting] 改档超过档位天花板: owner={owner_hasn_id}, node_id={node_id}, '
                    f'请求 {memory_mb}MiB, 天花板 {ceiling}MiB'
                )
                raise errors.ForbiddenError(
                    msg=f'当前订阅档最多支持单节点 {ceiling}MiB 内存，升级订阅后可调更高',
                    data={
                        'error': ERR_NODE_MEMORY_CEILING,
                        'requested_mb': memory_mb,
                        'ceiling_mb': ceiling,
                    },
                )

        try:
            agent_view = await hosting_agent_provider.resize_node(node_id, memory_mb=memory_mb, cpus=cpus)
        except HostingAgentError as exc:
            row.failure_reason = exc.failure_reason or FAILURE_INTERNAL_ERROR
            row.failure_detail = str(exc)
            await self.record_event(
                db,
                cloud_node=row,
                event_type=EVENT_FAILED,
                detail={
                    'stage': 'resize',
                    'error': exc.code,
                    'status_code': exc.status_code,
                    'requested_memory_mb': memory_mb,
                    'requested_cpus': cpus,
                },
            )
            await db.flush()
            log.error(f'[hosting] 改档失败: node_id={node_id}, err={exc}')
            raise errors.ServerError(msg=f'托管宿主执行改档失败：{exc}')

        # 回写**宿主回读的真实生效值**，不是我们请求过的值——两者混同会让改档失败被读成成功。
        applied_memory = agent_view.get('memory_mb')
        applied_cpus = agent_view.get('cpus')
        if isinstance(applied_memory, int) and applied_memory > 0:
            row.memory_mb = applied_memory
        if isinstance(applied_cpus, (int, float)) and applied_cpus > 0:
            row.cpus = float(applied_cpus)
        row.status = NODE_STATUS_STARTING
        row.failure_reason = None
        row.failure_detail = None
        await self.record_event(
            db,
            cloud_node=row,
            event_type=EVENT_RESIZED,
            detail={
                'requested_memory_mb': memory_mb,
                'requested_cpus': cpus,
                'applied_memory_mb': row.memory_mb,
                'applied_cpus': row.cpus,
            },
        )
        await db.flush()
        return await self._render_one(db, row, with_agent_view=True)

    async def reauthorize_node(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        owner_hasn_id: str,
        node_id: str,
    ) -> dict[str, Any]:
        """重新授权（§3.1）：**仅** `failed(credential_invalid)` 可用；铸新码 → agent 重注入并重启。

        数据卷不动——这条路径存在的意义就是「长期停止后 refresh token 过期」时救回节点而不丢数据。
        """
        row = await self._get_owned(db, owner_hasn_id=owner_hasn_id, node_id=node_id)
        if row.status != NODE_STATUS_FAILED or row.failure_reason != FAILURE_CREDENTIAL_INVALID:
            log.warning(
                f'[hosting] 重新授权被拒（状态不符）: node_id={node_id}, '
                f'status={row.status}, reason={row.failure_reason}'
            )
            raise errors.ForbiddenError(
                msg='仅设备凭据失效的节点可重新授权', data={'error': ERR_REAUTHORIZE_NOT_ALLOWED}
            )

        minted = await authorization_code_service.mint(
            db, user_id=user_id, owner_hasn_id=owner_hasn_id, node_id=node_id, purpose=CODE_PURPOSE_REAUTHORIZE
        )
        try:
            await hosting_agent_provider.reauthorize_node(node_id, authorization_code=minted.plain_code)
        except HostingAgentError as exc:
            row.failure_detail = str(exc)
            await self.record_event(
                db,
                cloud_node=row,
                event_type=EVENT_FAILED,
                detail={'stage': 'reauthorize', 'error': exc.code, 'status_code': exc.status_code},
            )
            await db.flush()
            log.error(f'[hosting] 重新授权失败: node_id={node_id}, err={exc}')
            raise errors.ServerError(msg=f'托管宿主重新授权失败：{exc}')

        row.status = NODE_STATUS_STARTING
        row.failure_reason = None
        row.failure_detail = None
        await self.record_event(db, cloud_node=row, event_type=EVENT_REAUTHORIZED, detail={})
        await db.flush()
        return await self._render_one(db, row)

    async def purge_node(
        self,
        db: AsyncSession,
        *,
        cloud_node: HasnCloudNodes,
        reason: str,
    ) -> dict[str, Any]:
        """销毁一个云端节点的**唯一**实现（设计 §7「删除」行）——顺序不可颠倒：

        1. 置 `deleting`（中断态可见，不假装还在服务）
        2. **吊销设备凭据**：JWT session + owner 绑定 + presence 断链
        3. 调 hosting-agent `DELETE /v1/nodes/{node_id}?purge_volume=true` 销毁容器与卷
        4. `hasn_nodes` 标记退役
        5. 托管行落 `deleted` + `deleted` 事件

        第 2 步必须在第 3 步之前：凭据先失效，容器即便还活着也够不到云端；反过来会留下
        「容器已删、凭据仍有效」的窗口。

        `reason` 进事件 detail，用于区分主人主动删除 / 保留期逾期 / 账号注销三条来路。
        第 3 步失败**不阻断退役**：凭据已吊销、节点已不可用，容器残留交宿主侧巡检回收，
        但真实错误必须留在 `failure_detail` 与事件里（零 fake：不谎称干净删除）。
        """
        node_id = cloud_node.node_id
        cloud_node.status = NODE_STATUS_DELETING
        await db.flush()

        await self.revoke_node_credentials(
            db, cloud_node=cloud_node, user_id=cloud_node.user_id, owner_hasn_id=cloud_node.owner_hasn_id
        )

        purge_failed: str | None = None
        try:
            await hosting_agent_provider.delete_node(node_id, purge_volume=True)
        except HostingAgentError as exc:
            # 凭据已吊销、节点已不可用；容器残留由宿主侧巡检回收 → 如实留痕但不阻断退役
            purge_failed = str(exc)
            log.error(f'[hosting] 销毁容器失败（凭据已吊销，容器残留待宿主回收）: node_id={node_id}, err={exc}')

        hasn_node = (
            await db.execute(select(HasnNodes).where(HasnNodes.node_id == node_id))
        ).scalar_one_or_none()
        if hasn_node is not None:
            hasn_node.status = 'deleted'

        cloud_node.status = NODE_STATUS_DELETED
        cloud_node.container_ref = None
        cloud_node.online_since = None
        cloud_node.retain_until = None
        if purge_failed:
            cloud_node.failure_detail = purge_failed
        await self.record_event(
            db,
            cloud_node=cloud_node,
            event_type=EVENT_DELETED,
            detail={'purge_volume': True, 'reason': reason, 'agent_error': purge_failed},
        )
        await db.flush()
        return {'node_id': node_id, 'deleted': True, 'agent_error': purge_failed}

    async def delete_node(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        owner_hasn_id: str,
        node_id: str,
    ) -> dict[str, Any]:
        """主人主动删除（§3.1 / 设计 §7）。归属校验在这里做，销毁顺序全交 `purge_node`。

        `user_id` 保留在签名里是为了让路由层的调用形态不变；真正吊销凭据用的是托管行上的
        `user_id`（权威在行上，避免调用方传错导致吊销到别人的 session）。
        """
        del user_id
        row = await self._get_owned(db, owner_hasn_id=owner_hasn_id, node_id=node_id)
        return await self.purge_node(db, cloud_node=row, reason='owner_requested')

    async def _purge_node_isolated(
        self,
        db: AsyncSession,
        *,
        cloud_node: HasnCloudNodes,
        reason: str,
    ) -> str | None:
        """`purge_node` 的错误隔离包装：成功回 None，失败回人可读原因。

        错误隔离收在本 helper 内（而非循环体里的 try/except），既满足 ruff PERF203，
        也保证「一个节点炸掉」不会让同一主人的其余节点停在「凭据已吊销、容器还在跑」的半路。
        """
        try:
            await self.purge_node(db, cloud_node=cloud_node, reason=reason)
        except Exception as exc:
            log.error(f'[hosting] 销毁节点失败: node_id={cloud_node.node_id}, reason={reason}, err={exc}')
            return str(exc)
        return None

    async def purge_for_account_deletion(self, db: AsyncSession, *, user_id: int) -> dict[str, Any]:
        """主人账号注销：立即销毁其全部云端节点，**不留保留期**（设计 §7 末行 / D-14）。

        与「订阅到期」路径的唯一区别就是没有 30 天缓冲——账号都没了，再留数据既无人可续订、
        也无处授权访问。逐节点独立处理：单个节点销毁抛错不该让其余节点留在半路（凭据已吊销、
        容器却还在跑）；错误如实计入返回值与日志。

        调用方负责在账号注销的同一事务里调用（本方法只 flush，不 commit）。
        """
        rows = (
            await db.execute(
                select(HasnCloudNodes).where(
                    HasnCloudNodes.user_id == user_id,
                    HasnCloudNodes.status != NODE_STATUS_DELETED,
                )
            )
        ).scalars().all()
        purged: list[str] = []
        failed: list[dict[str, str]] = []
        for row in rows:
            error = await self._purge_node_isolated(db, cloud_node=row, reason='account_deletion')
            if error is None:
                purged.append(row.node_id)
            else:
                failed.append({'node_id': row.node_id, 'error': error})
        if rows:
            log.info(f'[hosting] 账号注销销毁云端节点: user_id={user_id}, purged={len(purged)}, failed={len(failed)}')
        return {'purged': purged, 'failed': failed}

    @staticmethod
    async def revoke_node_credentials(
        db: AsyncSession,
        *,
        cloud_node: HasnCloudNodes,
        user_id: int,
        owner_hasn_id: str,
    ) -> None:
        """吊销云端节点的设备凭据：JWT session + owner 绑定 + presence。

        `credential_session_uuid` 是该容器兑换授权码时拿到的那一个 session，单独吊销它
        **不影响主人的其它设备**（主人 token 从不进容器，这里也绝不做全量清理）。
        """
        session_uuid = cloud_node.credential_session_uuid
        if session_uuid:
            await revoke_token(user_id, session_uuid)
            await redis_client.delete(f'{settings.TOKEN_REFRESH_REDIS_PREFIX}:{user_id}:{session_uuid}')
            cloud_node.credential_session_uuid = None
        await hasn_node_bindings_service.remove_owner_binding(
            db=db, node_id=cloud_node.node_id, owner_id=owner_hasn_id
        )
        await db.flush()
        await _node_session_gateway.disconnect_node(node_id=cloud_node.node_id)

    # ─── 票据 ───

    async def issue_access_ticket(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        owner_hasn_id: str,
        node_id: str,
    ) -> IssuedTicket:
        """签发节点访问票据（§3.4①）。归属校验在这里做，edge 只负责换 grant。"""
        row = await self._get_owned(db, owner_hasn_id=owner_hasn_id, node_id=node_id)
        return await access_ticket_service.issue_ticket(
            user_id=user_id,
            owner_hasn_id=owner_hasn_id,
            node_id=row.node_id,
            host=row.host,
        )

    # ─── 内部面（hosting-agent 回报） ───

    async def report_status(
        self,
        db: AsyncSession,
        *,
        node_id: str,
        status: str,
        host: str | None = None,
        failure_reason: str | None = None,
        failure_detail: str | None = None,
        container_ref: str | None = None,
        image_digest: str | None = None,
        image_version: str | None = None,
        last_backup_at: str | None = None,
    ) -> dict[str, Any]:
        """hosting-agent 回报容器事实（契约 §3.3 / §4.5②）。

        **agent 永不上报 `online`**：容器 running 时它只报 `starting` + `container_state=running`；
        `online` 与 `failed(daemon_not_online)` 的判定完全归云端，依据 Redis presence（D-13）。
        这里对 `online` 直接拒——静默降级会掩盖 agent 侧的契约违例，让「谁判在线」重新变得含糊。
        """
        row = (
            await db.execute(select(HasnCloudNodes).where(HasnCloudNodes.node_id == node_id))
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='云端节点不存在')
        if status == NODE_STATUS_ONLINE:
            log.warning(f'[hosting] agent 试图上报 online（在线判定归云端 presence），已拒: node_id={node_id}')
            raise errors.RequestError(
                msg='hosting-agent 不得上报 online：在线判定归云端 Redis presence（容器 running 请报 starting）'
            )
        if status not in NODE_STATUSES:
            raise errors.RequestError(msg=f'非法 status: {status}')
        if failure_reason is not None and failure_reason not in FAILURE_REASONS:
            raise errors.RequestError(msg=f'非法 failure_reason: {failure_reason}')

        row.status = status
        row.failure_reason = failure_reason
        row.failure_detail = failure_detail
        if host:
            # agent 带自己的 HOST_ID：多宿主下票据分发与调度按 host 走，必须以 agent 上报为准
            row.host = host
        if container_ref:
            row.container_ref = container_ref
        if image_digest:
            row.image_digest = image_digest
        if image_version:
            row.image_version = image_version
        if last_backup_at:
            try:
                row.last_backup_at = timezone.from_datetime(datetime.fromisoformat(last_backup_at))
            except ValueError:
                # 上游时间格式不对属可恢复的字段级问题：其余回报照落，只丢这一格 → warn
                log.warning(f'[hosting] last_backup_at 解析失败，保持原值: node_id={node_id}, raw={last_backup_at!r}')
        await db.flush()
        return {'ok': True, 'node_id': node_id, 'status': row.status}

    async def append_event(
        self,
        db: AsyncSession,
        *,
        node_id: str,
        event_type: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """hosting-agent 追加事件（契约 §3.3 / §4.5③）。event_type 限 §2.3 枚举。"""
        row = (
            await db.execute(select(HasnCloudNodes).where(HasnCloudNodes.node_id == node_id))
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='云端节点不存在')
        if event_type not in EVENT_TYPES:
            # 认不出的事件类型宁可拒，也不往事件流水里塞 UI 无法分支的脏值
            raise errors.RequestError(msg=f'非法 event_type: {event_type}')
        await self.record_event(db, cloud_node=row, event_type=event_type, detail=detail or {})
        return {'ok': True}

    async def pending_updates(self, db: AsyncSession) -> dict[str, Any]:
        """待滚动更新的节点清单（契约 §3.3 / §4.5④，hosting-agent 轮询）。

        判据：目标镜像 digest 与节点当前 digest 不一致，且节点不在 deleting/deleted/updating 态。
        每个条目**自带** `{node_id, image_ref, image_digest, image_version}`——agent 会跳过缺
        `node_id` 或 `image_ref` 的条目，把镜像信息只放在顶层等于让它整批跳过。
        没有已发布镜像时诚实返回空清单 + `image_available=false`，不臆造更新目标。
        """
        try:
            image_ref, digest, version = await self.resolve_image(db)
        except (errors.NotFoundError, errors.ServerError) as exc:
            log.warning(f'[hosting] pending-updates 无可用镜像: {exc}')
            return {'image_available': False, 'nodes': []}

        rows = (
            await db.execute(
                select(HasnCloudNodes).where(
                    HasnCloudNodes.status.notin_(
                        [NODE_STATUS_DELETED, NODE_STATUS_DELETING, NODE_STATUS_UPDATING]
                    )
                )
            )
        ).scalars().all()
        return {
            'image_available': True,
            'image_ref': image_ref,
            'image_digest': digest,
            'image_version': version,
            'nodes': [
                {
                    'node_id': r.node_id,
                    'host': r.host,
                    'current_digest': r.image_digest,
                    'image_ref': image_ref,
                    'image_digest': digest,
                    'image_version': version,
                }
                for r in rows
                if (r.image_digest or '') != digest
            ],
        }

    # ─── 订阅到期保留（设计 §7） ───

    @staticmethod
    async def mark_retention(db: AsyncSession, *, node_id: str, days: int = 30) -> datetime | None:
        """订阅到期：置 `stopped` 并落 30 天保留期截止（D-14）。返回保留期截止时刻。

        调用方是 `cloud_node_retention_sweep`（`backend/app/hasn_hosting/tasks.py`）：**必须先把容器
        真停掉再调本方法**，否则库里写着 stopped、容器还在跑，就是零 fake 铁律禁止的伪状态。
        逾期销毁由同一支 sweep 的第二个动作接手，不依赖人工运维。
        """
        row = (
            await db.execute(select(HasnCloudNodes).where(HasnCloudNodes.node_id == node_id))
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = NODE_STATUS_STOPPED
        row.online_since = None
        row.retain_until = timezone.now() + timedelta(days=days)
        await db.flush()
        return row.retain_until


cloud_node_service = CloudNodeService()
