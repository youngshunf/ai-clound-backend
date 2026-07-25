import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class LlmNewapiUserMapping(Base):
    """唤星用户与 new-api 用户映射表"""

    __tablename__ = 'llm_newapi_user_mapping'

    id: Mapped[id_key] = mapped_column(init=False)
    huanxing_user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='唤星 sys_user.id')
    newapi_user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='new-api users.id')
    newapi_token_key: Mapped[str] = mapped_column(sa.String(128), default='', comment='new-api tokens.key（用户默认 API Key）')
    newapi_token_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='new-api tokens.id')
    app_code: Mapped[str] = mapped_column(sa.String(32), default='', comment='应用标识 (huanxing:唤星/zhixiaoya:知小鸦)')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:启用:green/disabled:禁用:red)')
    # 经 HTTP 管理 API 解耦后新增（2026-06-15）：
    # new-api 无 admin 建 token 接口，建 relay token 必须以用户身份。故为每个映射缓存一份
    # 用户 access_token（Fernet 加密），复用于建/旋转 Agent token，避免每次重置密码重新登录。
    newapi_access_token: Mapped[str | None] = mapped_column(
        sa.String(512), default=None, comment='用户 new-api access_token（Fernet 加密，建 relay token 复用）'
    )
    # 两个「每小时增量回扣」的同步游标已随 doc94 D1 删除：
    # 云端不再对账余额，更不会按已用量算出目标 quota 覆盖写回 NewAPI。
