"""金融投研 6 类产物 + watchlist 的 `:sync` 入/出参 schema（05 §5.3a）。

daemon 侧 outbox 每条投影一次上行调用，请求体统一为 `op`（create/update/delete）+ 幂等键
（`op_id`）+ 乐观锁基线（`base_revision`）+ 本地身份（`local_ref`/`node_id`/`agent_hasn_id`）+
业务字段块（`fields`，各产物形状不同）。响应统一 `SyncResult(id, revision, op)`——`id` 是云端
权威 id（跨设备/分享打开的唯一依据），`revision` 供 daemon 更新本地 base 做下一轮乐观锁。

隐私红线（05 C5）：影子账户的原始对账单/流水/真实账号/本地绝对路径**永不上云**——`fields` 里
不得出现 `source_file_ref`/`source_content_hash`；`account_alias` 只存主人给的别名，绝不是真实账号。
端点层据本 schema 白名单剔除，云端表本就无这些隐私列。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from backend.common.schema import SchemaBase

# 三种同步动作：新建 / 更新 / 软删
SyncOp = Literal['create', 'update', 'delete']


class SyncEnvelope(SchemaBase):
    """`:sync` 请求信封基类——各产物端点复用同一形状，仅 `fields` 的业务键不同。"""

    op: SyncOp = Field(description='同步动作：create/update/delete')
    op_id: str = Field(description='outbox 操作幂等键（响应丢失重发时据此幂等回放，不重复应用/不误判冲突）')
    base_revision: int | None = Field(
        None, description='update/delete 的乐观锁基线（daemon 手上的 revision）；与云端当前不一致 → 409 带快照'
    )
    local_ref: str | None = Field(
        None, description='本地幂等键（daemon 本地行 id）。create 必填；仅做去重，云端从不据它解析/暴露/进 URI'
    )
    server_id: str | None = Field(None, description='云端权威 id。update/delete 必填')
    node_id: str | None = Field(None, description='产出设备节点 id（溯源）')
    agent_hasn_id: str | None = Field(None, description='产出分身 HASN ID。为空=主人手工建（跳过产物登记）')
    session_id: str | None = Field(None, description='工作会话 id（登记绑定，供工作会话资源栏展示）')
    project_id: str | None = Field(None, description='挂靠平台项目 id（doc38 层2，可空；仅聚合过滤键）')
    fields: dict[str, Any] = Field(default_factory=dict, description='业务字段块（各产物形状不同；端点层按白名单校验）')


class SyncResult(SchemaBase):
    """`:sync` 统一响应——云端权威 id + 云端 revision + 实际生效动作。"""

    id: str = Field(description='云端权威 id（跨设备/分享打开的唯一依据）')
    revision: int = Field(description='云端单调版本（daemon 据此更新本地 base，做下一轮乐观锁）')
    op: SyncOp = Field(description='实际生效的动作')
