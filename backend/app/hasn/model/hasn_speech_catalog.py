"""通用语音模型签名目录模型（云端权威·哑存储·单行下发）SPCAT-4。

一行权威（config_key='global'）承载「离线签名的语音模型 catalog」全文：
  - catalog_json：发布方离线 Ed25519 私钥签名的 catalog 逐字节原文（TEXT，非 JSONB）。
    daemon 持内置公钥自行验签——云端只哑存储 + 下发，不验签、不改写（同 hasn_release minisign 哲学）。
    ⚠️ 绝不用 JSONB / 解析后重序列化：daemon verify 会 serde 反序列化 payload 重算签名，
    任何字段增删或 JSON 归一都会破坏验签。
  - 模型 zip 包托管公开桶（category=speech_model，长效 https），URL 内嵌在签名 catalog 里。

形态属「配置/元数据单行」（非实体 CRUD），与 hasn_platform_default_config / hasn_app_catalog 同为
public-schema 配置表，故沿用其 ``Base`` 手写约定（id + created_time/updated_time 由 Base 提供），
不走 4-scope fba 代码生成；节点 GET + CI 发布由 service/API 自定义。
表结构以 ``backend/sql/hasn/hasn_speech_catalog.sql`` 为准。
"""

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnSpeechCatalog(Base):
    """通用语音模型签名目录（云端权威·哑存储·单行下发）。"""

    __tablename__ = 'hasn_speech_catalog'

    id: Mapped[id_key] = mapped_column(init=False)
    config_key: Mapped[str] = mapped_column(
        sa.String(32), default='global', unique=True, comment='配置键（单行权威，恒 global）'
    )
    catalog_json: Mapped[str] = mapped_column(
        sa.Text(), default='', comment='离线签名的 catalog 逐字节原文（daemon 验签用，绝不解析后重序列化）'
    )
    revision: Mapped[str] = mapped_column(
        sa.String(16), default='', comment='catalog 原文指纹 sha256(catalog_json)[:16]，daemon 比对重拉'
    )
    catalog_version: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='catalog 内声明的版本号（展示/回滚判定用，非权威）'
    )
    model_summary: Mapped[list] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='模型摘要（仅管理端展示，非权威）'
    )
    published_by: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='最后发布方标识（CI/发布者标签）'
    )
