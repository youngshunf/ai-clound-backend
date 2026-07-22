"""hasn_im.application.event_appender · integration_events 追加（event_seq 乱序防护·R2-04）

**§7.2 P0 硬要求**：`event_seq` 分配须防提交乱序。序列取号在事务内发生，提交顺序 ≠ 取号顺序
时，先取号后提交的事务其 seq 已低于消费者水位、被**永久跳过**（等于把 §1.2「消息存在但事件
不可见」以并发窗口重新引入）。`hasn_sync_events.revision` 的 MAX+1 竞态已在线上炸过一次
（cloud `8a125cdf`，per-owner advisory xact lock 修复）。本日志强制沿用同一先例：

- 同一事务内先 `pg_advisory_xact_lock(shard)` 再 `MAX(event_seq)+1` 分配——后到的事务阻塞到
  前一个提交后再读 MAX，拿到正确下一个值；锁随 commit/rollback 自动释放，保证**同分片内
  seq 顺序 == 提交顺序**，水位式消费（last_acked_seq）才安全；
- **事件插入放事务末尾**（调用方须在业务写全部完成后、commit 前最后调 `append_event`），
  使临界区最小——锁只在「取号→插入→commit」这段极短窗口内持有。

**分片（§7.2）**：`shard_key` 初期恒为全局常量 ``_GLOBAL_SHARD=0``（分片数=1，当前量级足够）；
容量需要时按 ``hash(aggregate_id) % N`` 分片，消费者各持 ``consumer_name.shard{i}`` 独立 cursor
横向扩展，**表结构无需变更**。锁按分片命名空间化（``hashtext('...shard.' || shard)``），与
`hasn_sync_events` 的 owner 锁分处不同键、互不阻塞。

**payload「完整」口径（§7.2）**：以消费者**不出 IM 域**即可处理为准；禁把消息全文内嵌进
integration event 与 sync event 各存一份（那是同一正文的三份存储）。

事务契约：`append_event` 接调用方 `db`，与业务写**同一事务**落库（不 commit，由调用方提交）。
R2 期物理表 ``public.hasn_im_integration_events``（R2-11 迁 ``hasn_im`` schema）。
"""

from __future__ import annotations

import json
import uuid

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

# 分片数=1（§7.2）：全局单分片常量。容量需要时改 hash(aggregate_id)%N，消费者按 shard 各持 cursor。
_GLOBAL_SHARD = 0

# advisory 锁的命名空间前缀：按 shard 拼串再 hashtext，落到与 owner 锁（8a125cdf）不同的键空间，
# 保证 IM 事件序号分配与 sync revision 分配互不阻塞。
_SHARD_LOCK_PREFIX = 'hasn_im.integration_events.shard.'

# 物理表名（R2 期落 public·带 hasn_im_ 前缀；R2-11 SET SCHEMA → hasn_im 去前缀）
_TABLE = 'public.hasn_im_integration_events'


@dataclass(frozen=True)
class IntegrationEventRef:
    """已落库集成事件引用（append_event 的返回值·值对象）。"""

    event_seq: int
    event_id: str
    event_type: str
    shard_key: int


async def append_event(
    db: AsyncSession,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    aggregate_seq: int | None = None,
    trace_id: str | None = None,
    causation_id: str | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
    shard_key: int = _GLOBAL_SHARD,
) -> IntegrationEventRef:
    """在调用方事务内向 integration_events 追加一条事件，返回含分配 event_seq 的引用。

    **必须在业务写全部落库后、commit 前最后调用**（临界区最小化·§7.2）。event_seq 由本函数
    在锁内分配，调用方不得自带 seq；`event_id` 缺省自动生成（全局去重键）。
    """
    # 1) 先取分片级 advisory xact lock，串行化本分片 event_seq 分配（8a125cdf 先例）。
    #    hashtext(前缀 || shard) 落到独立键空间，锁在 commit/rollback 时释放。
    await db.execute(
        sa.text('SELECT pg_advisory_xact_lock(hashtext(:lock_key))'),
        {'lock_key': f'{_SHARD_LOCK_PREFIX}{shard_key}'},
    )
    # 2) 锁内 MAX+1 分配：后到事务已阻塞到前者提交，读到的 MAX 一定包含前者的行 → 无空洞无冲突。
    seq_row = await db.execute(
        sa.text(
            f'SELECT COALESCE(MAX(event_seq), 0) + 1 AS event_seq '  # noqa: S608 表名为内部常量非用户输入
            f'FROM {_TABLE} WHERE shard_key = :shard_key'
        ),
        {'shard_key': shard_key},
    )
    event_seq = int(seq_row.mappings().one()['event_seq'])
    resolved_event_id = event_id or f'ie_{uuid.uuid4().hex[:26]}'
    # 3) 插入放事务末尾（临界区最小）。occurred_at 缺省由 DB 取 now()。
    await db.execute(
        sa.text(
            f'INSERT INTO {_TABLE} ('  # noqa: S608 表名为内部常量非用户输入
            '  event_seq, event_id, event_type, aggregate_type, aggregate_id,'
            '  aggregate_seq, shard_key, payload, trace_id, causation_id, occurred_at'
            ') VALUES ('
            '  :event_seq, :event_id, :event_type, :aggregate_type, :aggregate_id,'
            '  :aggregate_seq, :shard_key, CAST(:payload AS jsonb), :trace_id, :causation_id,'
            '  COALESCE(:occurred_at, now())'
            ')'
        ),
        {
            'event_seq': event_seq,
            'event_id': resolved_event_id,
            'event_type': event_type,
            'aggregate_type': aggregate_type,
            'aggregate_id': aggregate_id,
            'aggregate_seq': aggregate_seq,
            'shard_key': shard_key,
            'payload': _json_dumps(payload or {}),
            'trace_id': trace_id,
            'causation_id': causation_id,
            'occurred_at': occurred_at,
        },
    )
    return IntegrationEventRef(
        event_seq=event_seq,
        event_id=resolved_event_id,
        event_type=event_type,
        shard_key=shard_key,
    )


def _json_dumps(payload: dict[str, Any]) -> str:
    """把 payload 序列化为 JSON 文本（交给 CAST(... AS jsonb) 落库）。"""
    return json.dumps(payload, ensure_ascii=False, default=str)
