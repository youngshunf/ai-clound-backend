"""记忆提取任务（doc 04 §11，原 public.memory_extraction_jobs，去前缀 extraction_job）。

记忆抽取 worker 的任务队列云端镜像。**镜像本地 crate 结构**：ULID 主键、epoch ms 时间戳，
与本地字段名严格双端一致（doc 04 §14）。继承 MappedBase 手写 schema；本模型仅元数据登记
（ADR-15 收编 P3）。
"""

import sqlalchemy as sa

from backend.common.model import MappedBase

_SCHEMA = 'hasn_memory'


class ExtractionJob(MappedBase):
    """HASN 记忆系统 - 提取任务。"""

    __tablename__ = 'extraction_job'
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')", name='ck_extraction_job_status'
        ),
        sa.UniqueConstraint(
            'agent_id', 'conversation_id', 'window_end_msg_id', 'trigger_reason', name='uq_extraction_job_window'
        ),
        {'comment': 'HASN 记忆系统 - 提取任务', 'schema': _SCHEMA},
    )

    job_id = sa.Column(sa.String(40), primary_key=True, comment='Job ID')
    agent_id = sa.Column(sa.String(40), nullable=False, comment='Agent ID')
    owner_id = sa.Column(sa.String(40), nullable=False, comment='Owner ID')
    conversation_id = sa.Column(sa.String(40), nullable=False, comment='会话 ID')
    window_start_msg_id = sa.Column(sa.String(40), nullable=False, comment='窗口起始消息 ID')
    window_end_msg_id = sa.Column(sa.String(40), nullable=False, comment='窗口结束消息 ID')
    trigger_reason = sa.Column(sa.String(40), nullable=False, comment='触发原因')
    source_dispatch_mode = sa.Column(sa.String(16), comment='来源 dispatch 模式')
    status = sa.Column(sa.String(16), nullable=False, comment='状态 (queued/running/succeeded/failed/skipped)')
    attempt = sa.Column(sa.Integer, nullable=False, server_default='0', comment='尝试次数')
    scheduled_at = sa.Column(sa.BigInteger, nullable=False, comment='调度时间 (epoch ms)')
    started_at = sa.Column(sa.BigInteger, comment='开始时间 (epoch ms)')
    completed_at = sa.Column(sa.BigInteger, comment='完成时间 (epoch ms)')
    error_code = sa.Column(sa.String(40), comment='错误码')
