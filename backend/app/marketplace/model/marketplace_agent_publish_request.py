import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.marketplace.model._base import MarketplaceBase
from backend.common.model import id_key


class MarketplaceAgentPublishRequest(MarketplaceBase):
    """Agent 市场发布幂等请求"""

    __tablename__ = 'marketplace_agent_publish_request'

    id: Mapped[id_key] = mapped_column(init=False)
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='发起发布的 Agent HASN ID')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='资源所属主人 HASN ID')
    resource_kind: Mapped[str] = mapped_column(sa.String(20), default='', comment='资源类型 (skill:技能:blue/template:模板:green/skill_pack:技能包:cyan)')
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), default='', comment='调用方生成的服务端去重键')
    asset_uri: Mapped[str] = mapped_column(sa.String(255), default='', comment='经 Owner ACL 验证的 hasn://asset/{id}')
    content_hash: Mapped[str] = mapped_column(sa.String(128), default='', comment='服务端解包后计算的规范化内容指纹，仅用于冲突检测')
    file_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='服务端读取资产字节后计算的 SHA256')
    resource_id: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='首次提交创建或更新的权威资源 ID')
    version: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='首次提交解析出的资源版本')
    state: Mapped[str] = mapped_column(sa.String(24), default='', comment='请求状态 (processing:处理中:orange/committed:已提交:green/partial:部分成功:yellow/failed:失败:red)')
    result: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='首次已提交结果，重复请求原样回放')
    work_session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='daemon 可信注入的工作会话 ID')
