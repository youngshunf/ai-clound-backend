import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.external_mcp.model._base import ExternalMcpAppBase
from backend.common.model import UniversalText, id_key


class ExternalMcpSecret(ExternalMcpAppBase):
    """secret:// 凭据密文存储（Fernet 加密；明文不落库/不回显/不下发；10 §7.1 生命周期）"""

    __tablename__ = 'external_mcp_secret'

    id: Mapped[id_key] = mapped_column(init=False)
    secret_uri: Mapped[str] = mapped_column(sa.String(256), default='', comment='secret:// 引用 URI（如 secret://system/qcc/bearer-token；全局唯一）')
    origin: Mapped[str] = mapped_column(sa.String(20), default='', comment='凭据归属 (system:平台/owner:用户/marketplace:市场)')
    owner_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='owner-origin 凭据归属主人 hasn_id（system-origin 为空）')
    ciphertext: Mapped[str] = mapped_column(UniversalText, default='', comment='密文（key_encryption Fernet 加密；明文经此加密落库，仅建连时解密注入）')
