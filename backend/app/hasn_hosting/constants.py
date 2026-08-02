"""无头 hasn-node 托管 · 全模块共用常量（H2 + H4）。

事实源：`docs/hasn-node设计文档/云端节点托管/实施/01-切片实施契约(H1-H8).md` §1 / §2.4 / §3。
本文件是云端侧唯一口径：状态机取值、失败原因码、错误码、Redis key 前缀、授权码有效期。
hosting-agent（H3）与 edge（H5）跨仓对齐时以本文件 + 契约为准。
"""

from __future__ import annotations

from typing import Final

# ─── 服务注册名（backend/common/service_registry.py 登记项） ───

HOSTING_SERVICE_NAME: Final[str] = 'hosting'

# ─── 授权码（§2.1） ───

#: 授权码有效期（秒）：签发 + 5 分钟
AUTHORIZATION_CODE_TTL_SECONDS: Final[int] = 300

#: 授权码用途
CODE_PURPOSE_CREATE: Final[str] = 'create'
CODE_PURPOSE_REAUTHORIZE: Final[str] = 'reauthorize'
CODE_PURPOSES: Final[tuple[str, ...]] = (CODE_PURPOSE_CREATE, CODE_PURPOSE_REAUTHORIZE)

#: 授权码状态
CODE_STATUS_PENDING: Final[str] = 'pending'
CODE_STATUS_CONSUMED: Final[str] = 'consumed'
CODE_STATUS_EXPIRED: Final[str] = 'expired'
CODE_STATUS_REVOKED: Final[str] = 'revoked'

# ─── 托管节点状态机（§2.2） ───

NODE_STATUS_PROVISIONING: Final[str] = 'provisioning'
NODE_STATUS_STARTING: Final[str] = 'starting'
NODE_STATUS_ONLINE: Final[str] = 'online'
NODE_STATUS_STOPPED: Final[str] = 'stopped'
NODE_STATUS_UPDATING: Final[str] = 'updating'
NODE_STATUS_FAILED: Final[str] = 'failed'
NODE_STATUS_DELETING: Final[str] = 'deleting'
NODE_STATUS_DELETED: Final[str] = 'deleted'

NODE_STATUSES: Final[tuple[str, ...]] = (
    NODE_STATUS_PROVISIONING,
    NODE_STATUS_STARTING,
    NODE_STATUS_ONLINE,
    NODE_STATUS_STOPPED,
    NODE_STATUS_UPDATING,
    NODE_STATUS_FAILED,
    NODE_STATUS_DELETING,
    NODE_STATUS_DELETED,
)

# ─── failure_reason 固定枚举（§2.4，UI 按此分支文案） ───

FAILURE_SUBSCRIPTION_INVALID: Final[str] = 'subscription_invalid'
FAILURE_CODE_EXPIRED: Final[str] = 'authorization_code_expired'
FAILURE_CODE_CONSUMED: Final[str] = 'authorization_code_consumed'
FAILURE_CREDENTIAL_INVALID: Final[str] = 'credential_invalid'
FAILURE_RESOURCE_EXHAUSTED: Final[str] = 'resource_exhausted'
FAILURE_IMAGE_PULL_FAILED: Final[str] = 'image_pull_failed'
FAILURE_CONTAINER_CRASHED: Final[str] = 'container_crashed'
FAILURE_DAEMON_NOT_ONLINE: Final[str] = 'daemon_not_online'
FAILURE_INTERNAL_ERROR: Final[str] = 'internal_error'

FAILURE_REASONS: Final[tuple[str, ...]] = (
    FAILURE_SUBSCRIPTION_INVALID,
    FAILURE_CODE_EXPIRED,
    FAILURE_CODE_CONSUMED,
    FAILURE_CREDENTIAL_INVALID,
    FAILURE_RESOURCE_EXHAUSTED,
    FAILURE_IMAGE_PULL_FAILED,
    FAILURE_CONTAINER_CRASHED,
    FAILURE_DAEMON_NOT_ONLINE,
    FAILURE_INTERNAL_ERROR,
)

# ─── 事件类型（§2.3） ───

EVENT_CREATED: Final[str] = 'created'
EVENT_STARTED: Final[str] = 'started'
EVENT_STOPPED: Final[str] = 'stopped'
EVENT_UPDATED: Final[str] = 'updated'
EVENT_UPDATE_FAILED: Final[str] = 'update_failed'
EVENT_ROLLED_BACK: Final[str] = 'rolled_back'
EVENT_REAUTHORIZED: Final[str] = 'reauthorized'
EVENT_DELETED: Final[str] = 'deleted'
EVENT_BACKUP: Final[str] = 'backup'
EVENT_FAILED: Final[str] = 'failed'
# 改资源档位（H9-b）。与 `updated` 分开：那条是换镜像，这条是改内存/CPU，
# 事件流水里混成一种会让「这次重启到底为什么」查不清楚。
EVENT_RESIZED: Final[str] = 'resized'

EVENT_TYPES: Final[tuple[str, ...]] = (
    EVENT_CREATED,
    EVENT_STARTED,
    EVENT_STOPPED,
    EVENT_UPDATED,
    EVENT_UPDATE_FAILED,
    EVENT_ROLLED_BACK,
    EVENT_REAUTHORIZED,
    EVENT_RESIZED,
    EVENT_DELETED,
    EVENT_BACKUP,
    EVENT_FAILED,
)

# ─── 错误码（响应 msg 之外的机器可判别标识；跨端 UI 按此分支） ───

ERR_QUOTA_EXCEEDED: Final[str] = 'cloud_node_quota_exceeded'
ERR_SUBSCRIPTION_REQUIRED: Final[str] = 'cloud_node_subscription_required'
ERR_CODE_NOT_FOUND: Final[str] = 'code_not_found'
ERR_CODE_EXPIRED: Final[str] = 'code_expired'
ERR_CODE_CONSUMED: Final[str] = 'code_consumed'
ERR_TICKET_INVALID: Final[str] = 'access_ticket_invalid'
ERR_GRANT_INVALID: Final[str] = 'session_grant_invalid'
ERR_GRANT_REPLAYED: Final[str] = 'session_grant_replayed'
ERR_SERVICE_UNCONFIGURED: Final[str] = 'service_unconfigured'
ERR_REAUTHORIZE_NOT_ALLOWED: Final[str] = 'reauthorize_not_allowed'
# 改档超过订阅档允许的单节点内存上限（H9-b）。与 ERR_QUOTA_EXCEEDED 分开：
# 那条是「节点数量」超了，这条是「单个节点太大」，WebUI 的引导文案完全不同
# （前者要删节点或加购，后者要升档）。
ERR_NODE_MEMORY_CEILING: Final[str] = 'cloud_node_memory_ceiling_exceeded'
ERR_RATE_LIMITED: Final[str] = 'rate_limited'

# ─── Redis key（§3.4） ───

#: 节点访问票据：`hasn:node_access_ticket:{sha256(ticket)}`，TTL 60s，一次性
ACCESS_TICKET_REDIS_PREFIX: Final[str] = 'hasn:node_access_ticket'
ACCESS_TICKET_TTL_SECONDS: Final[int] = 60

#: 会话授予 grant 的 jti 一次性消费标记：`hasn:node_session_grant_jti:{jti}`
GRANT_JTI_REDIS_PREFIX: Final[str] = 'hasn:node_session_grant_jti'
#: grant 有效期（秒）；jti 标记 TTL 取同值 + 余量，过期后自然回收
GRANT_TTL_SECONDS: Final[int] = 60
GRANT_JTI_TTL_SECONDS: Final[int] = 300

#: 授权码兑换限流：`hasn:node_code_exchange_rl:{维度}:{值}`
CODE_EXCHANGE_RATE_LIMIT_PREFIX: Final[str] = 'hasn:node_code_exchange_rl'
#: 同一 IP 每窗口最多兑换尝试次数
CODE_EXCHANGE_RATE_LIMIT_PER_IP: Final[int] = 20
#: 同一 code_hash 每窗口最多兑换尝试次数（防暴力重放）
CODE_EXCHANGE_RATE_LIMIT_PER_CODE: Final[int] = 5
CODE_EXCHANGE_RATE_LIMIT_WINDOW_SECONDS: Final[int] = 300

# ─── 会话授予 JWT（§3.4③） ───

GRANT_ISSUER: Final[str] = 'huanxing-cloud'
GRANT_AUDIENCE: Final[str] = 'hasn-node'
GRANT_TYP: Final[str] = 'node_session_grant'
GRANT_ALGORITHM: Final[str] = 'HS256'

# ─── 镜像（§1 / §7） ───

IMAGE_PLATFORM_TARGETS: Final[tuple[str, ...]] = ('headless-linux-amd64', 'headless-linux-arm64')
IMAGE_ASSET_KIND: Final[str] = 'image'

# ─── 配额（§3.1） ───

#: 档位未定档云端节点附赠数时的兜底值——**fail-closed 为 0**。
#: 主人 2026-08-01 五档定档（free/lite/pro/max/ultra）后只有 `pro` 及以上附赠 1 个；
#: 新档位若漏写 `max_cloud_nodes`，兜底给 1 就等于免费级别的档白得一个常驻容器。
#: 送资源必须是显式定档的结果，没定档就是 0（并 warn 让它显性暴露）。
TIER_GRANT_FALLBACK: Final[int] = 0

#: 订阅准入判定用的 feature_key（已注册进 `billing.core.feature_registry.FIXED_FEATURE_KEYS`）。
#: 注意与 `webapp:hosting` 区分：那个托管的是主人的**网页应用**（doc06 全栈托管），
#: 本键托管的是主人的**分身节点**（无头 hasn-node 容器）。
CLOUD_NODE_FEATURE_KEY: Final[str] = 'cloud_node'

#: 云端节点**加购**商品（超出档位附赠部分按需购买）：¥128/月 · ¥1228/年，与 llm:tier 的 pro 同价同折扣
CLOUD_NODE_OFFERING_KEY: Final[str] = 'cloud:node'

#: LLM 订阅商品键——按 tier 名反查档位附赠的云端节点数时用（存量合同不写 offering_key）
LLM_TIER_OFFERING_KEY: Final[str] = 'llm:tier'
