"""hasn_client 应用独立 PG schema 基类与 schema 常量。

每个 AI-Native / companion 应用使用独立 PG schema（ADR-15：命名空间与目录约定）。
PG schema 统一用 `hasn_` 前缀（app_id 仍为 `client`，URL 仍为 /api/v1/app/* —— 本模块由历史扁平
`app/models` + `app/api/v1/app` 按收编方案 09 R2 归位、R3 隔离 schema 而来，URL 兼容不变）。

本应用的 model 是**手写命令式**（imperative `sa.Column` + tuple `__table_args__` 带 Index/
UniqueConstraint），不是 codegen 声明式，因此**无法靠继承基类的 declared_attr 注入 schema**
（tuple `__table_args__` 会覆盖基类的 dict）。约定：手写模型在各自 tuple `__table_args__`
的结尾 dict 里显式写 `'schema': APP_SCHEMA`（见 push_token/push_receipt/feature_flag/
telemetry_event 等）。`HasnClientBase` 供**未来 codegen 新增表**继承（自动落 schema）。

共享表（身份 public.hasn_humans/hasn_agents、用户 public.sys_user 等）仍留原 schema，
跨 schema 全限定引用。

⚠️ 存量表搬迁（ADR-15 §5 §7）：6 张表（push_tokens / push_token_audit / push_receipts /
feature_flags / feature_flag_assignments / telemetry_events）已存在于 public，由
`backend/sql/hasn_client/migrations/2026-06-15-move-client-tables-to-hasn-client-schema.sql`
幂等 `ALTER TABLE ... SET SCHEMA hasn_client` 搬迁。
⚠️ 凡裸 raw SQL（`text(...)`）引用本应用表必须显式全限定 `hasn_client.<table>`
（无 search_path 配置时默认只解析 public，搬迁后裸名/`public.` 前缀都会失效）。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（hasn_ 前缀；app_id=client，URL=/api/v1/app/*，见上）
APP_SCHEMA = 'hasn_client'


class HasnClientBase(Base):
    """hasn_client 应用模型基类（供未来 codegen 新增表继承）：

    bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_client。
    现有手写命令式模型用 tuple `__table_args__`，在各自结尾 dict 里直接写 `'schema': APP_SCHEMA`，
    不继承本基类（tuple 会覆盖此处的 declared_attr dict）。
    """

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
