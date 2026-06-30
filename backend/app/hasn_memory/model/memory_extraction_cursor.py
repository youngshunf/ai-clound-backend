"""记忆提取游标（doc16 Phase C2：单一云端提取 worker 的每-owner 增量水位）。

云端提取 worker 改为「单一云端管线」后，需要一个**每 owner 的增量水位**记录「上次提取处理到
哪条消息」，以便：
- 增量：每次只取 `hasn_messages.id > last_message_id` 的新消息（owner 输入 + 任务结果/摘要）；
- 幂等：重复触发不重复提取同一窗口（按 message id 单调推进）。

继承 MappedBase 手写 schema（与 `semantic_fact` / `extraction_job` 同族：epoch ms 时间戳，
schema=hasn_memory）；本模型仅元数据登记。游标无 API 面，仅 worker 内部读写。
"""

import sqlalchemy as sa

from backend.common.model import MappedBase

_SCHEMA = 'hasn_memory'


class MemoryExtractionCursor(MappedBase):
    """HASN 记忆系统 - 提取游标（每 owner 增量水位）。"""

    __tablename__ = 'memory_extraction_cursor'
    __table_args__ = ({'comment': 'HASN 记忆系统 - 提取游标', 'schema': _SCHEMA},)

    owner_id = sa.Column(sa.String(40), primary_key=True, comment='Owner ID')
    last_message_id = sa.Column(sa.BigInteger, nullable=False, server_default='0', comment='已提取 msg id')
    last_session_checkpoint_at = sa.Column(sa.BigInteger, nullable=False, server_default='0', comment='摘要水位 ms')
    facts_written = sa.Column(sa.BigInteger, nullable=False, server_default='0', comment='累计写入事实数')
    last_run_at = sa.Column(sa.BigInteger, nullable=False, server_default='0', comment='上次运行 epoch ms')
    created_at = sa.Column(sa.BigInteger, nullable=False, comment='创建时间 (epoch ms)')
    updated_at = sa.Column(sa.BigInteger, nullable=False, comment='更新时间 (epoch ms)')
