from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnJudgeVerdict(Base):
    """通用LLM裁判判定记录（doc07 §5.3：教师标签+可观测，全kind共表）"""

    __tablename__ = 'hasn_judge_verdict'

    id: Mapped[id_key] = mapped_column(init=False)
    judge_kind: Mapped[str] = mapped_column(sa.String(32), default='', comment='裁判类型 (termination:会话终止:blue/disclosure:隐私披露:green)')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='发起方分身所属主人 hasn_id（凭据/计费归属）')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='发起方分身 hasn_id')
    peer_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='对端 hasn_id（人或分身）')
    conversation_ref: Mapped[str] = mapped_column(sa.String(64), default='', comment='daemon 本地会话 id，仅溯源元数据，不作资源解析（URI 铁律豁免范围）')
    input_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='脱敏后裁判输入（transcript/正文+上下文；L1 命中片段以 PartialMask 形态入库，不存附件/原文）')
    verdict_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='裁判出参 JSON（kind 专属：termination={should_end,reason}；disclosure={allow,categories,reason}）')
    model: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='实际命中的裁判模型名')
    latency_ms: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='LLM 调用耗时（毫秒）')
