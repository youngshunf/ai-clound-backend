"""R3 schema 硬切换的显式命名。

切换前后都返回完整物理表名；生产切换后绝不依赖 ``search_path``。ORM 模型只取
``im_schema`` / ``sync_schema``，raw SQL 则取完整表名。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SchemaNames:
    """根据 R3 开关解析 ORM schema 与 raw SQL 表名。"""

    cutover: bool

    @property
    def im_schema(self) -> str:
        return 'hasn_im' if self.cutover else 'public'

    @property
    def sync_schema(self) -> str:
        return 'hasn_sync' if self.cutover else 'public'

    def im_table(self, table: str) -> str:
        return f'hasn_im.{table}' if self.cutover else f'public.{table}'

    def im_event_table(self, table: str) -> str:
        return f'hasn_im.{table}' if self.cutover else f'public.hasn_im_{table}'

    def sync_table(self, table: str) -> str:
        return f'hasn_sync.{table}' if self.cutover else f'public.{table}'


def configured_schema_names() -> SchemaNames:
    """从全局配置构造当前进程固定的 schema 名称。"""
    from backend.core.conf import settings

    return SchemaNames(cutover=settings.HASN_IM_SCHEMA_CUTOVER)


SCHEMA_NAMES = configured_schema_names()
IM_SCHEMA = SCHEMA_NAMES.im_schema
SYNC_SCHEMA = SCHEMA_NAMES.sync_schema
