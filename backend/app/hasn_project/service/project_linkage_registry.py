"""平台项目「挂靠点注册表」—— link/unlink 的唯一收口（doc38 §3.5 / §5 item 5）。

**禁止 project service 跨 schema 散写 UPDATE**：把「哪些资源域能挂靠进项目、挂靠列在哪张表哪一列、
owner 怎么校验」收成一份注册表；`hasn.project.link/unlink` 只解析 `hasn://` URI → 查注册表 → 由
adapter 落挂靠列。新增可挂靠资源域 = 注册一行 adapter（U11 各容器加 `platform_project_id` 列后
逐个注册），不改工具、不散写。

两类挂靠点（doc38 §4）：
- **artifact 级**（U3 即活）：`hasn_artifacts.project_id`——产物直接打标。register-on-write 经
  ContextVar **自动打标只进不退**（仅空列写入）；`link/unlink` 是**显式改挂/摘除**通道（可覆盖已有值、
  可清空），对齐 doc38「自动打标只进不退：改挂/摘除只能显式操作」。
- **容器级**（U11 逐个注册）：知识库/deck/图坊项目/获客项目/站点等各自容器表的 `platform_project_id`
  列——把整个容器挂进项目。U3 尚无容器列，故只注册 artifact adapter；容器 adapter 是「加一行」的增量。

owner 隔离：adapter 定位资源行时按 `owner_column == owner` 过滤（非本人 → 404），跨 owner 挂不进。
目标项目归属由调用方（工具）先经 `project_service.get_project` 校验，不重复判。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _err(code: str, msg: str) -> errors.RequestError:
    return errors.RequestError(msg=msg, data={'error_code': code})


def parse_hasn_uri(uri: str) -> tuple[str, str]:
    """`hasn://<domain>/<server_id>` → (domain, server_id)。

    domain 可多段（如 `reel/projects`、`plan/goals`）：server_id = 最后一段，domain = 其余段。
    非法（无 scheme / 缺 id）→ 业务 400。**server_id 必须云端权威 id**（守「本地 ID 永不上 URI」铁律；
    本函数只做语法解析，权威性由 adapter 用它查库时兜——查不到即 404）。
    """
    raw = (uri or '').strip()
    if not raw.startswith('hasn://'):
        raise _err('invalid_uri', f'资源地址必须是 hasn:// URI，收到：{uri!r}')
    body = raw[len('hasn://') :]
    segs = [s for s in body.split('/') if s != '']
    if len(segs) < 2:
        raise _err('invalid_uri', f'hasn:// URI 缺少资源 id 段：{uri!r}')
    server_id = segs[-1]
    domain = '/'.join(segs[:-1])
    return domain, server_id


@dataclass(frozen=True)
class LinkageAdapter:
    """一个可挂靠资源域的挂靠点声明（表 + 定位列 + owner 列 + 挂靠列）。

    - `domain`：doc36 URI 域（`hasn://{domain}/{id}` 的 host+path 前缀）。
    - `model` / `id_column`：按 `{server_id}` 定位资源行的表与列。
    - `owner_column`：owner 隔离列（定位时强制 `== owner`）。
    - `attach_column`：落项目挂靠的列（artifact 级=`project_id`；容器级=`platform_project_id`）。
    - `id_is_uuid`：`{server_id}` 是否需转 UUID 再比对（UUID 主键列为 True）。
    - `is_container`：True=容器级（`platform_project_id`，参与并集读反查）；False=artifact 级。
    """

    domain: str
    model: type
    id_column: str
    owner_column: str
    attach_column: str
    id_is_uuid: bool = False
    is_container: bool = False


class ProjectLinkageRegistry:
    """挂靠点注册表单例：域 → adapter；link/unlink 经此收口，绝不散写。"""

    def __init__(self) -> None:
        self._adapters: dict[str, LinkageAdapter] = {}

    def register(self, adapter: LinkageAdapter) -> None:
        self._adapters[adapter.domain] = adapter

    def get(self, domain: str) -> LinkageAdapter | None:
        return self._adapters.get(domain)

    def container_adapters(self) -> list[LinkageAdapter]:
        """容器级 adapter（`platform_project_id`）——并集读反查用（U3 为空，U11 起有值）。"""
        return [a for a in self._adapters.values() if a.is_container]

    async def _locate(self, db: AsyncSession, adapter: LinkageAdapter, owner: str, server_id: str) -> Any:
        """按 (owner, server_id) 定位资源行；不存在/非本人 → 404（owner 隔离兜死）。"""
        id_col = getattr(adapter.model, adapter.id_column)
        owner_col = getattr(adapter.model, adapter.owner_column)
        value: Any = server_id
        if adapter.id_is_uuid:
            try:
                value = UUID(server_id)
            except (ValueError, AttributeError, TypeError) as e:
                raise _err('invalid_uri', f'{adapter.domain} 资源 id 不是合法 UUID：{server_id!r}') from e
        row = (
            await db.execute(sa.select(adapter.model).where(id_col == value, owner_col == owner))
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='要挂靠的资源不存在或不属于你')
        return row

    async def link(self, db: AsyncSession, *, owner: str, resource_uri: str, project_id: str | UUID) -> dict:
        """把 `resource_uri` 指向的资源挂靠进项目（写 adapter 的挂靠列）。project 归属由调用方先校验。"""
        domain, server_id = parse_hasn_uri(resource_uri)
        adapter = self.get(domain)
        if adapter is None:
            raise _err('unsupported_link_domain', f'资源域「{domain}」暂不支持挂靠进项目')
        row = await self._locate(db, adapter, owner, server_id)
        pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
        setattr(row, adapter.attach_column, pid)
        await db.flush()
        return {'linked': True, 'resource_uri': resource_uri, 'project_id': str(pid)}

    async def unlink(self, db: AsyncSession, *, owner: str, resource_uri: str) -> dict:
        """把 `resource_uri` 指向的资源从项目摘出（挂靠列置 NULL）。"""
        domain, server_id = parse_hasn_uri(resource_uri)
        adapter = self.get(domain)
        if adapter is None:
            raise _err('unsupported_link_domain', f'资源域「{domain}」暂不支持挂靠进项目')
        row = await self._locate(db, adapter, owner, server_id)
        setattr(row, adapter.attach_column, None)
        await db.flush()
        return {'unlinked': True, 'resource_uri': resource_uri}


project_linkage_registry = ProjectLinkageRegistry()

# ── U3 唯一活的挂靠点：artifact 级（hasn_artifacts.project_id）──────────────────────
# 产物按 `hasn://artifact/{artifact_id}` 显式改挂/摘除（与 register-on-write 自动打标「只进不退」互补）。
# 容器级 adapter（knowledge/deck/imagelab/growth 的 platform_project_id）随 U11 各容器加列后逐个注册。
project_linkage_registry.register(
    LinkageAdapter(
        domain='artifact',
        model=HasnArtifacts,
        id_column='artifact_id',
        owner_column='owner_hasn_id',
        attach_column='project_id',
        id_is_uuid=False,
        is_container=False,
    )
)
