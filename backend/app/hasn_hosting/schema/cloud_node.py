"""无头 hasn-node 托管的对外契约 schema（Owner 面 / 节点面 / 内部面）。

**授权码永远不出现在任何响应 schema 里**——它只在服务端从铸造点直达 hosting-agent。
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


# ─── Owner 面（/api/v1/hasn/app/cloud-nodes） ───


class CloudNodeEventView(SchemaBase):
    """托管事件（详情端点返回，倒序最多 20 条）。"""

    event_type: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_time: str | None = None


class CloudNodeView(SchemaBase):
    """云端节点对外视图（契约 §3.1.1）。`status='online'` 当且仅当 `presence_online=True`。

    **刻意不含** `container_ref` / `credential_session_uuid`：那是运维内部信息，
    云端 app 面与 daemon 都不下发给主人面。
    """

    model_config = ConfigDict(extra='allow')

    node_id: str = Field(description='节点 node_id（与 hasn_nodes 一致）')
    node_name: str | None = Field(default=None, description='设备显示名（权威在 hasn_nodes）')
    host: str = Field(description='承载宿主标识')
    status: str = Field(description='provisioning/starting/online/stopped/updating/failed/deleting/deleted')
    failure_reason: str | None = Field(
        default=None,
        description=(
            '失败原因码（契约 §2.4 固定九项）：subscription_invalid / authorization_code_expired / '
            'authorization_code_consumed / credential_invalid / resource_exhausted / image_pull_failed / '
            'container_crashed / daemon_not_online / internal_error'
        ),
    )
    failure_detail: str | None = Field(default=None, description='人可读失败详情')
    presence_online: bool = Field(description='Redis presence 是否命中（在线判定唯一依据）')
    image_version: str | None = Field(default=None, description='当前镜像版本')
    image_digest: str | None = Field(default=None, description='当前镜像 digest')
    update_available: bool = Field(
        default=False, description='当前 digest 与 stable 渠道最新 headless 镜像 digest 不一致'
    )
    latest_image_version: str | None = Field(default=None, description='最新 headless 镜像版本；无发布时 null')
    backup_configured: bool | None = Field(
        default=None,
        description='透传 hosting-agent /health 的同名字段；false=此节点数据无备份，null=宿主探活不通（未知）',
    )
    retain_until: str | None = Field(default=None, description='订阅到期后的数据保留截止')
    last_backup_at: str | None = Field(default=None, description='最近卷备份时刻；null=尚无备份（如实显示）')
    online_since: str | None = Field(default=None, description='本次上线起始时刻')
    created_time: str | None = None
    updated_time: str | None = None
    # 以下三项由 hosting-agent `GET /v1/nodes/{node_id}` 原样透传（契约 §4.5⑤），仅详情端点返回；
    # 宿主不可达时为 null（未知），绝不用「看起来正常」的默认值顶替。
    daemon_endpoint: str | None = Field(default=None, description='容器 daemon 内网地址（edge 的转发目标）')
    egress_guard_active: bool | None = Field(default=None, description='宿主出网内网禁令是否已生效')
    events: list[CloudNodeEventView] | None = Field(default=None, description='最近事件（仅详情端点返回）')


class CreateCloudNodeRequest(SchemaBase):
    """创建云端节点入参（当前无可调参数；保留占位以便后续加档位/区域而不破坏契约）。"""

    node_name: str | None = Field(default=None, max_length=100, description='节点显示名（可选）')


class AccessTicketResponse(SchemaBase):
    """节点访问票据（§3.4①）。"""

    ticket: str = Field(description='一次性票据明文，60s 有效')
    expires_at: str = Field(description='过期时刻 ISO8601')
    edge_url: str = Field(description='edge 反代基址；未配置时为空串（如实留空，不臆造域名）')


class DeleteCloudNodeResult(SchemaBase):
    """删除结果。"""

    node_id: str
    deleted: bool
    agent_error: str | None = Field(default=None, description='容器销毁失败时的真实原因（凭据已吊销）')


# ─── 节点面（/api/v1/hasn/node/cloud，无 Owner JWT） ───


class ExchangeAuthorizationCodeRequest(SchemaBase):
    """授权码兑换入参（§3.2）。"""

    code: str = Field(min_length=8, max_length=256, description='授权码明文')
    node_id: str = Field(min_length=1, max_length=40, description='预分配的 node_id')


class ExchangeAuthorizationCodeResponse(SchemaBase):
    """兑换成功返回的设备级凭据。"""

    access_token: str
    refresh_token: str
    expires_in: int = Field(description='access_token 有效期（秒）')
    user_id: int
    owner_hasn_id: str
    node_id: str


class VerifySessionGrantRequest(SchemaBase):
    """会话授予校验入参（§3.4⑥）。"""

    grant: str = Field(min_length=16, description='edge 转交的 grant JWT')


class VerifySessionGrantResponse(SchemaBase):
    """校验通过返回的三元组；daemon 仍须自校验 node_id / owner_hasn_id（D-18 第二道墙）。"""

    node_id: str
    owner_hasn_id: str
    user_id: int


# ─── 内部面（/api/v1/hasn/internal/cloud-nodes） ───


class RedeemAccessTicketRequest(SchemaBase):
    """edge 核销票据（§3.4③）。"""

    ticket: str = Field(min_length=8, max_length=256)


class RedeemAccessTicketResponse(SchemaBase):
    """核销结果：edge 据此路由到 host 上的容器，并把 grant 交给 daemon。"""

    node_id: str
    owner_hasn_id: str
    host: str
    grant: str


class ReportNodeStatusRequest(SchemaBase):
    """hosting-agent 回报容器事实（契约 §3.3 / §4.5②）。

    **`status='online'` 会被拒**：agent 永不上报 online，容器 running 时只报 `starting`；
    在线与 `failed(daemon_not_online)` 的判定完全归云端 Redis presence（D-13）。
    """

    status: str = Field(description='provisioning/starting/stopped/updating/failed/deleting/deleted')
    host: str | None = Field(default=None, description='agent 自己的 HOST_ID，落 hasn_cloud_nodes.host')
    failure_reason: str | None = Field(default=None, description='契约 §2.4 固定九项之一')
    failure_detail: str | None = None
    container_ref: str | None = None
    image_digest: str | None = None
    image_version: str | None = None
    last_backup_at: str | None = Field(default=None, description='最近卷备份时刻 ISO8601')


class AppendNodeEventRequest(SchemaBase):
    """hosting-agent 追加事件（契约 §3.3 / §4.5③）。"""

    event_type: str = Field(
        min_length=1,
        max_length=32,
        description='created/started/stopped/updated/update_failed/rolled_back/reauthorized/deleted/backup/failed',
    )
    detail: dict[str, Any] = Field(default_factory=dict)
