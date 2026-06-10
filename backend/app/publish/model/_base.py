"""publish 应用（模块 18，通用网页发布与分享）独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
⚠️ 命名不对称：app_id=`publish`，但 PG schema/表前缀=`webpublish`（避免与动词 publish 混淆）。
本基类在 fba `Base`（bigint `id_key` + DateTimeMixin）之上注入 `schema='webpublish'`，
使本应用 model 继承它即落到 `webpublish.*` schema，无需逐表手写 `__table_args__`。
共享表（身份 public.hasn_humans/hasn_agents、资产 public.hasn_assets）仍留 public，跨 schema 全限定引用。

设计事实源：docs/hasn-node设计文档/18-通用网页发布与分享/01-数据模型与权限.md §2。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（≠ app_id，见上）
APP_SCHEMA = 'webpublish'


class PublishAppBase(Base):
    """publish 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=webpublish。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
