"""external_mcp 应用独立 PG schema 基类（ADR-15：AI-Native 应用命名空间与目录约定）。

第三方 MCP 网关的四张表（server/binding/secret/usage）落到独立 schema `external_mcp`，
与身份表（public.hasn_humans/hasn_agents）、资产表（public.hasn_assets）等共享表隔离，
跨 schema 全限定引用。本基类在 fba `Base`（bigint `id_key` + DateTimeMixin）之上注入
`schema='external_mcp'`，使各 model 继承它即落到 `external_mcp.*`，无需逐表手写 `__table_args__`。

设计事实源：docs/hasn-node设计文档/MCP统一工具体系/10-MCP网关与第三方MCP接入.md。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名
APP_SCHEMA = 'external_mcp'


class ExternalMcpAppBase(Base):
    """external_mcp 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=external_mcp。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
