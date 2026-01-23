"""
凭证同步数据模型

@author Ysf
@date 2025-12-28
"""


import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class SyncedCredential(Base):
    """同步凭证表 - 存储用户从桌面端同步的加密凭证"""

    __tablename__ = "synced_credential"

    id: Mapped[id_key] = mapped_column(init=False)

    # === 必填字段（无默认值）必须放在前面 ===

    # 用户关联
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        index=True,
        comment="用户ID"
    )

    # 凭证标识
    platform: Mapped[str] = mapped_column(
        sa.String(64),
        index=True,
        comment="平台名称"
    )
    account_id: Mapped[str] = mapped_column(
        sa.String(128),
        index=True,
        comment="账号ID"
    )

    # 加密数据 (端到端加密，服务端无法解密)
    encrypted_data: Mapped[bytes] = mapped_column(
        sa.LargeBinary,
        comment="加密的凭证数据"
    )

    # 同步密钥哈希 (用于验证客户端身份)
    sync_key_hash: Mapped[str] = mapped_column(
        sa.String(128),
        comment="同步密钥哈希"
    )

    # === 可选字段（有默认值）放在后面 ===

    account_name: Mapped[str] = mapped_column(
        sa.String(256),
        default="",
        comment="账号名称"
    )

    # 数据版本 (用于冲突检测)
    version: Mapped[int] = mapped_column(
        default=1,
        comment="数据版本号"
    )

    # 客户端信息
    client_id: Mapped[str | None] = mapped_column(
        sa.String(128),
        default=None,
        comment="客户端设备ID"
    )

    # 状态
    status: Mapped[int] = mapped_column(
        default=1,
        comment="状态(0禁用 1正常)"
    )

    __table_args__ = (
        sa.UniqueConstraint("user_id", "platform", "account_id", name="uq_user_platform_account"),
        {"comment": "同步凭证表"},
    )
