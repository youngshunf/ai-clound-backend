"""workbench 应用独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
PG schema 统一用 `hasn_` 前缀（app_id 仍为 `workbench`）。本模块由 `app/hasn` 中的工作台
简报/偏好子域（hasn_workbench_briefing / hasn_workbench_briefing_feedback / hasn_owner_workbench_pref）
按 ADR-15 §4 抽出为独立应用 app/workbench；**URL 前缀保持 `/api/v1/hasn/app/workbench/*` 不变**
（daemon `modules/huanxing/workbench.rs`→`domains/workbench/cloud.rs::WorkbenchCloud` 代理路径依赖）。
本基类在 fba `Base`（bigint `id_key` + DateTimeMixin）之上注入 `schema='hasn_workbench'`，
使各 model 继承它即落到 `hasn_workbench.*` schema。

平台底座留 app/hasn（不随本应用迁出）：workbench_domain_service（应用域/工作空间解析，
被 enterprise/knowledge/workspace/ai_native 共用）、workbench_app_registry（ADR-15 M5 待退役）、
workbench_event_bus；内置任务目录服务已在 app/hasn_task（本应用 API 直连）。身份 public.hasn_agents
跨 schema 全限定引用。

⚠️ 存量表搬迁（ADR-15 §5 §7）：3 张表已存在于 public，由
`backend/sql/workbench/migrations/2026-06-14-move-workbench-tables-to-hasn-workbench-schema.sql`
幂等 `ALTER TABLE ... SET SCHEMA hasn_workbench` 搬迁；ORM 经本基类自动落到新 schema。
⚠️ 凡裸 raw SQL 引用本应用表须显式全限定 `hasn_workbench.<table>`（当前全 ORM，无裸 SQL）。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（hasn_ 前缀；app_id=workbench，见上）
APP_SCHEMA = 'hasn_workbench'


class WorkbenchBase(Base):
    """workbench 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_workbench。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
