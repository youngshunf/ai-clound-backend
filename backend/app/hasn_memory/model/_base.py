"""记忆子系统独立 PG schema 基类（ADR-15：AI-Native 应用一应用一 schema、`hasn_` 前缀）。

记忆系统（doc 01 §6 / doc 04 §15）本就被定位为独立子系统：本地权威在 hasn-node
`crates/hasn-memory`（SQLite 主存储 + 索引），云端 `hasn_memory` schema 作为镜像 + 多端
同步中枢。本基类在 fba `Base`（bigint `id_key` + DateTimeMixin）之上注入 `schema='hasn_memory'`，
使各标准 fba model 继承它即落到 `hasn_memory.*`。

⚠️ 镜像本地 crate 的非标准表（namespace_revision、episodic_turn 等：ULID/复合主键 + epoch ms
时间戳、无 fba DateTimeMixin）**不继承本基类**，而是直接继承 `MappedBase` 并手写 `__table_args__`
注入 schema（见各 model 文件），与本地 crate 字段名严格双端一致（doc 04 §14 禁漂移）。

⚠️ 存量表搬迁（ADR-15 §5 §7）：owner_memory 两表 + namespace_revision + 4 张设计表族原在 public，
由 `backend/sql/hasn_memory/migrations/2026-06-15-move-memory-tables-to-hasn-memory-schema.sql`
幂等 `ALTER TABLE ... SET SCHEMA hasn_memory`（+ 去前缀 RENAME）搬迁；ORM 经本基类/各 model
自动落到新 schema。凡裸 raw SQL（`text(...)`）引用记忆表必须显式全限定 `hasn_memory.<table>`
（无 search_path 时默认只解析 public，搬迁后裸名/`public.` 前缀都会失效——billing 踩过的坑）。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（hasn_ 前缀；app_id=hasn_memory）
APP_SCHEMA = 'hasn_memory'


class HasnMemoryBase(Base):
    """记忆应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_memory。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
