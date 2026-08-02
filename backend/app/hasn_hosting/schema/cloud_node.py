"""无头 hasn-node 托管的对外契约 schema（Owner 面 / 节点面 / 内部面）。

**授权码永远不出现在任何响应 schema 里**——它只在服务端从铸造点直达 hosting-agent。
"""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, model_validator

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
    # 资源档位（H9-b）。装了引擎的节点内存要上调，UI 得先能如实显示「现在什么规格」。
    memory_mb: int = Field(default=0, description='单节点内存上限 MiB；0=尚未从宿主回报（不是「限 0 字节」，UI 显示未知）')
    cpus: float = Field(default=0.0, description='单节点 CPU 配额（核数）；0=尚未从宿主回报')
    disk_used_mb: int | None = Field(
        default=None, description='数据卷实际占用 MiB；null=测不出来（**不是 0**，0 会被读成没占空间）'
    )
    disk_quota_active: bool = Field(
        default=False,
        description=(
            '数据卷是否有硬配额。当前恒 false —— Docker 具名卷默认不限大小，容器内 df 看到的是'
            '整个宿主盘。UI 不得据此画「已用 x/y」的进度条（分母根本不存在）'
        ),
    )
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


class ResizeCloudNodeRequest(SchemaBase):
    """改资源档位入参（H9-b）。**镜像与数据卷都不动**，宿主按新规格重建容器。

    典型场景：主人在该节点的 WebUI 装了图坊/语音引擎，内存需求跳一档——容器里的 daemon
    改不了自己的 cgroup 上限，只能从宿主侧调。

    省略的维度保持当前值；**两个都省略是无意义请求**，422 打回，不静默变成空操作
    （那会让调用方以为「改过了」）。
    """

    memory_mb: int | None = Field(default=None, ge=256, description='新的内存上限 MiB，受订阅档天花板约束')
    cpus: float | None = Field(default=None, gt=0, description='新的 CPU 配额（核数）')

    @model_validator(mode='after')
    def _at_least_one(self) -> ResizeCloudNodeRequest:
        if self.memory_mb is None and self.cpus is None:
            raise ValueError('memory_mb 与 cpus 至少要给一个，否则这次改档没有任何含义')
        return self


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
