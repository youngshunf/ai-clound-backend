"""工作流执行态上行同步契约（daemon → cloud，doc36 §6.3 · U5a）。

**为什么需要这条通道**：工作流的调度与执行权威在 daemon（`persistence/workflow.rs`），云端
`workflow_run` / `workflow_node_run` 两表 2026-07-14 由 P1 expand-only 迁移建好、DAO 也写了读方法，
但**从来没有写者**——没有构造点、没有 INSERT、同步管线不含它。于是云端表是空壳，任何「按场景 run
聚合产物」的云端查询（doc36 §6.2 `hasn.workflow.run_artifacts`）查的都是空表。本模块补上写者。

**契约形状**对齐既有 `run_summary` 上行范式（`hasn_sync_service.save_task_run_summary`）：
幂等 UPSERT（键 `workflow_run_uuid` / `node_run_uuid`——模型注释本就自称「同步主键」）+ 越权校验。
**凭据用 Owner JWT 而非 Agent JWT**（与 run_summary 的差异，理由见 `workflow_sync_service` docstring）。

时间字段收 `datetime | int | float | str`：daemon SQLite 存的是 INTEGER Unix 秒，Pydantic v2 原生
按 Unix 时间戳解析 int/float 为 UTC aware datetime，无需自写转换。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import Field

from backend.common.schema import SchemaBase
from backend.app.hasn_task.schema.workflow import CreateWorkflowParam


class WorkflowRunUpstream(SchemaBase):
    """一次整图 fire 的执行实例（本地 `workflow_runs` 一行）。"""

    workflow_run_uuid: str = Field(description='端云稳定执行实例 UUID（幂等键）')
    workflow_uuid: str = Field(description='所属工作流稳定 UUID')
    workflow_name_snapshot: Annotated[str | None, Field(description='fire 时的工作流名称快照')] = None
    template_key_snapshot: Annotated[str | None, Field(description='fire 时的场景模板键快照')] = None
    project_id: Annotated[UUID | None, Field(description='云端权威平台项目 UUID；空值表示未挂项目的历史')] = None
    dedupe_key: str | None = Field(None, description='幂等键 workflow_uuid:fire_at（缺省取 workflow_run_uuid）')
    status: str | None = Field(None, description='running/completed/failed/blocked/cancelled')
    advance_mode: str | None = Field(None, description='推进档位 manual/auto')
    scheduled_fire_at: datetime | int | float | str | None = Field(None, description='触发时刻')
    graph_snapshot: dict[str, Any] | None = Field(None, description='fire 时固化的 nodes+edges 快照')
    output_summary: str | None = Field(None, description='整图终态综合')
    started_at: datetime | int | float | str | None = Field(None, description='开始时间')
    finished_at: datetime | int | float | str | None = Field(None, description='完成时间')


class WorkflowNodeRunUpstream(SchemaBase):
    """一个节点在本次 fire 内的执行态（本地 `workflow_node_runs` 一行）。

    `work_session_id` 与 `artifacts` 是 doc36 §6 的正主：前者把节点连到工作会话，后者是节点产物
    清单——汇总节点靠这两列才能跨节点把产物找齐。
    """

    node_run_uuid: str = Field(description='端云稳定节点执行 UUID（幂等键）')
    workflow_run_uuid: str = Field(description='所属执行实例 UUID')
    workflow_uuid: str = Field(description='所属工作流稳定 UUID（冗余便于查询）')
    node_key: str = Field(description='图内节点标识')
    status: str = Field(description='节点执行态（十态 ∪ 调度器过渡态 success/error）')
    work_session_id: str | None = Field(None, description='最新工作会话 id')
    artifacts: list[dict[str, Any]] | None = Field(
        None, description='产出物 [{artifact_id,kind,is_current,version,session_id}]'
    )
    output_summary: str | None = Field(None, description='产出摘要')
    output_gate_retries: int | None = Field(None, ge=0, description='产出闸重试次数')
    review_rejects: int | None = Field(None, ge=0, description='质量门驳回次数')
    attention_reason: str | None = Field(None, description='需要处理的原因')
    started_at: datetime | int | float | str | None = Field(None, description='开始时间')
    completed_at: datetime | int | float | str | None = Field(None, description='完成时间')


class WorkflowNodeRunsSyncRequest(SchemaBase):
    """一次上行批：可只带 runs、只带 node_runs，或两者同批（节点终态时通常两者都推）。"""

    owner_id: str | None = Field(None, description='Owner HASN ID；缺省取 Owner JWT 身份')
    sync_protocol_version: int = Field(1, ge=1, description='1=兼容孤儿历史；2=缺父定义或父 run 时逐条 deferred')
    runs: list[WorkflowRunUpstream] = Field(default_factory=list, description='整图执行实例')
    node_runs: list[WorkflowNodeRunUpstream] = Field(default_factory=list, description='节点执行态')


class WorkflowNodeRunsSyncResponse(SchemaBase):
    """逐条结果：接受数 + 被拒条目（**不整批失败**）。

    一条坏行不该把同批的好行一起拖走——daemon 是按「节点状态变化」批量推的，整批 4xx 会让
    本来没问题的节点行也永远上不来，而 daemon 侧只会把整批标失败重推、下一轮继续撞同一条坏行。
    """

    accepted_runs: int = Field(ge=0, description='成功 upsert 的执行实例数')
    accepted_node_runs: int = Field(ge=0, description='成功 upsert 的节点执行态数')
    rejected: list[dict[str, Any]] = Field(default_factory=list, description='被拒条目 [{uuid, reason}]')
    deferred: list[dict[str, Any]] = Field(default_factory=list, description='暂缓条目 [{uuid, reason}]')


class WorkflowDefinitionImport(SchemaBase):
    """旧 daemon 工作流定义的 create-only 导入快照。"""

    workflow: CreateWorkflowParam = Field(description='完整 workflow + nodes + edges 定义')


class WorkflowDefinitionsSyncRequest(SchemaBase):
    """旧定义导入请求；新版实例化不得走本接口。"""

    owner_id: str | None = Field(None, description='仅与 Owner JWT 一致时允许')
    sync_protocol_version: int = Field(2, ge=1, description='1=兼容孤儿执行态；2=缺父定义 deferred')
    definitions: list[WorkflowDefinitionImport] = Field(default_factory=list, max_length=200)


class WorkflowDefinitionsSyncResponse(SchemaBase):
    """旧定义导入逐条结果。"""

    created: list[str] = Field(default_factory=list, description='首次创建的 workflow_uuid')
    idempotent: list[str] = Field(default_factory=list, description='定义哈希一致的重放 workflow_uuid')
