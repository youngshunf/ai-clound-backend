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
    ERR_QUOTA_EXCEEDED,
    ERR_REAUTHORIZE_NOT_ALLOWED,
    ERR_SUBSCRIPTION_REQUIRED,
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_FAILED,
    EVENT_REAUTHORIZED,
    EVENT_STARTED,
    EVENT_STOPPED,
    EVENT_TYPES,
    FAILURE_CREDENTIAL_INVALID,
    FAILURE_DAEMON_NOT_ONLINE,
    FAILURE_INTERNAL_ERROR,
    FAILURE_REASONS,
    IMAGE_ASSET_KIND,
    NODE_STATUS_DELETED,
    NODE_STATUS_DELETING,
    NODE_STATUS_FAILED,
    NODE_STATUS_ONLINE,
    NODE_STATUS_PROVISIONING,
    NODE_STATUS_STARTING,
    NODE_STATUS_STOPPED,
    NODE_STATUS_UPDATING,
    NODE_STATUSES,
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


class CloudNodeService:
    """Owner 面 + 内部面共用的托管编排。"""

    # ─── 订阅与配额 ───

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
    async def _quota_of(db: AsyncSession, subscription: UserSubscription) -> int:
        """该订阅允许的云端节点数上限。

        取值顺序：合同快照 `plan_snapshot.max_cloud_nodes` → 商品档位 `billing_plan.quota_json`
        → 配置默认（`HOSTING_MAX_NODES_PER_OWNER`，默认 1）。档位没写就是没写，不臆造上限。
        """
        snapshot = subscription.plan_snapshot or {}
        raw = snapshot.get('max_cloud_nodes')
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
                raw = (plan.quota_json or {}).get('max_cloud_nodes')
        if raw is None:
            return int(settings.HOSTING_MAX_NODES_PER_OWNER)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            log.warning(f'[hosting] 订阅 {subscription.id} 的 max_cloud_nodes 非法（{raw!r}），回落配置默认值')
            return int(settings.HOSTING_MAX_NODES_PER_OWNER)

    async def _assert_can_create(self, db: AsyncSession, *, user_id: int, owner_hasn_id: str) -> None:
        """创建前置闸：统一准入判定 + 有效订阅 + 未超配额。任一不过即抛带错误码的 4xx。"""
        decision = await resolve_access(
            db, feature_key=CLOUD_NODE_FEATURE_KEY, subject_type='owner', subject_id=owner_hasn_id
        )
        if not decision.allowed:
            log.warning(f'[hosting] 创建云端节点被商业化内核拦下: owner={owner_hasn_id}, reason={decision.reason}')
            raise errors.ForbiddenError(
                msg=f'当前订阅不支持云端节点（{decision.reason}）',
                data={'error': ERR_SUBSCRIPTION_REQUIRED, 'reason': decision.reason},
            )

        subscription = await self._active_subscription(db, user_id=user_id)
        if subscription is None:
            log.warning(f'[hosting] 创建云端节点被拒（无有效订阅）: user_id={user_id}')
            raise errors.ForbiddenError(
                msg='需要有效订阅才能创建云端节点', data={'error': ERR_SUBSCRIPTION_REQUIRED}
            )

        quota = await self._quota_of(db, subscription)
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
            log.warning(f'[hosting] 云端节点配额超限: owner={owner_hasn_id}, used={used}, quota={quota}')
            raise errors.ForbiddenError(
                msg=f'已达云端节点配额上限（{used}/{quota}）',
                data={'error': ERR_QUOTA_EXCEEDED, 'used': used, 'quota': quota},
            )

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

    async def delete_node(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        owner_hasn_id: str,
        node_id: str,
    ) -> dict[str, Any]:
        """删除（§3.1 / 设计 §7）：先吊销设备凭据，再销毁容器与卷，最后把 `hasn_nodes` 标记退役。

        顺序不可颠倒：凭据先失效，容器即便还活着也够不到云端；反过来会留下「容器已删、凭据仍有效」的窗口。
        """
        row = await self._get_owned(db, owner_hasn_id=owner_hasn_id, node_id=node_id)
        row.status = NODE_STATUS_DELETING
        await db.flush()

        await self.revoke_node_credentials(db, cloud_node=row, user_id=user_id, owner_hasn_id=owner_hasn_id)

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

        row.status = NODE_STATUS_DELETED
        row.container_ref = None
        row.online_since = None
        if purge_failed:
            row.failure_detail = purge_failed
        await self.record_event(
            db,
            cloud_node=row,
            event_type=EVENT_DELETED,
            detail={'purge_volume': True, 'agent_error': purge_failed},
        )
        await db.flush()
        return {'node_id': node_id, 'deleted': True, 'agent_error': purge_failed}

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
    async def mark_retention(db: AsyncSession, *, node_id: str, days: int = 30) -> None:
        """订阅到期：置 `stopped` 并落 30 天保留期截止（逾期由运维流程销毁卷）。"""
        row = (
            await db.execute(select(HasnCloudNodes).where(HasnCloudNodes.node_id == node_id))
        ).scalar_one_or_none()
        if row is None:
            return
        row.status = NODE_STATUS_STOPPED
        row.retain_until = timezone.now() + timedelta(days=days)
        await db.flush()


cloud_node_service = CloudNodeService()
