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
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable
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
    - `app_id` / `kind` / `title_column`：项目总览挂靠资源行的通用展示元数据。
    - `revision_column` / `sync_kind`：显式挂靠变更时递增业务版本并推对应 owner 失效信号。
    - `related_resource_uris`：容器名下历史产物 URI 派生钩子；由应用 adapter 自己实现关系查询，
      project service 不得跨 schema 特判。
    """

    domain: str
    model: type
    id_column: str
    owner_column: str
    attach_column: str
    id_is_uuid: bool = False
    is_container: bool = False
    app_id: str | None = None
    kind: str | None = None
    title_column: str | None = None
    revision_column: str | None = None
    sync_kind: str | None = None
    related_resource_uris: Callable[[Any, str, tuple[Any, ...]], Awaitable[list[str]]] | None = None


class ProjectLinkageRegistry:
    """挂靠点注册表单例：域 → adapter；link/unlink 经此收口，绝不散写。"""

    def __init__(self) -> None:
        self._adapters: dict[str, LinkageAdapter] = {}

    def register(self, adapter: LinkageAdapter) -> None:
        self._adapters[adapter.domain] = adapter

    def get(self, domain: str) -> LinkageAdapter | None:
        return self._adapters.get(domain)

    def container_adapters(self) -> list[LinkageAdapter]:
        """容器级 adapter（`platform_project_id`）——挂靠资源与并集读反查用。"""
        return [a for a in self._adapters.values() if a.is_container]

    @staticmethod
    def _active_condition(adapter: LinkageAdapter) -> Any | None:
        """若容器模型有 status 列，则统一排除 tombstone；无状态列的容器不额外过滤。"""
        status_col = getattr(adapter.model, 'status', None)
        return status_col != 'deleted' if status_col is not None else None

    async def _attached_rows(
        self,
        db: AsyncSession,
        adapter: LinkageAdapter,
        *,
        owner: str,
        project_id: UUID,
    ) -> tuple[Any, ...]:
        """按 owner + 项目取某 adapter 的全部有效容器行。"""
        owner_col = getattr(adapter.model, adapter.owner_column)
        attach_col = getattr(adapter.model, adapter.attach_column)
        conditions = [owner_col == owner, attach_col == project_id]
        active = self._active_condition(adapter)
        if active is not None:
            conditions.append(active)
        rows = (await db.execute(sa.select(adapter.model).where(*conditions))).scalars().all()
        return tuple(rows)

    async def list_linked_resources(
        self,
        db: AsyncSession,
        *,
        owner: str,
        project_id: str | UUID,
    ) -> list[dict[str, Any]]:
        """列项目已挂靠的容器，供项目总览复用 descriptor 深链。

        只遍历注册表的容器 adapter；watchlist、本机安装态、隐私授权等未注册对象天然不会出现。
        """
        pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
        resources: list[dict[str, Any]] = []
        for adapter in self.container_adapters():
            for row in await self._attached_rows(db, adapter, owner=owner, project_id=pid):
                server_id = getattr(row, adapter.id_column)
                resource_uri = f'hasn://{adapter.domain}/{server_id}'
                title = (
                    getattr(row, adapter.title_column, None)
                    if adapter.title_column is not None
                    else None
                )
                updated = getattr(row, 'updated_time', None)
                resources.append(
                    {
                        'resource_uri': resource_uri,
                        'app_id': adapter.app_id or adapter.domain.split('/', 1)[0],
                        'kind': adapter.kind or adapter.domain,
                        'title': str(title) if title not in (None, '') else resource_uri,
                        'linked_time': updated.isoformat() if isinstance(updated, datetime) else None,
                    }
                )
        resources.sort(key=lambda item: (str(item['app_id']), str(item['kind']), str(item['resource_uri'])))
        return resources

    async def artifact_resource_uris(
        self,
        db: AsyncSession,
        *,
        owner: str,
        project_id: str | UUID,
    ) -> list[str]:
        """派生项目挂靠容器本体及其名下历史产物 URI。

        project 域只负责遍历 adapter；容器到子产物的业务关系由各应用的
        `related_resource_uris` 钩子查询，避免 project 模块出现跨 schema if/else。
        """
        pid = project_id if isinstance(project_id, UUID) else UUID(str(project_id))
        uris: set[str] = set()
        for adapter in self.container_adapters():
            rows = await self._attached_rows(db, adapter, owner=owner, project_id=pid)
            uris.update(f'hasn://{adapter.domain}/{getattr(row, adapter.id_column)}' for row in rows)
            if rows and adapter.related_resource_uris is not None:
                uris.update(await adapter.related_resource_uris(db, owner, rows))
        return sorted(uris)

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
        else:
            # 非 UUID 不等于字符串：finance 等容器使用 BIGINT 主键。按 ORM 列声明解析，
            # 避免 PostgreSQL 出现 bigint = varchar；字符串主键的 python_type 仍会原样得到 str。
            try:
                python_type = id_col.type.python_type
                value = python_type(server_id)
            except (AttributeError, TypeError, ValueError) as e:
                raise _err('invalid_uri', f'{adapter.domain} 资源 id 类型非法：{server_id!r}') from e
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
        previous = getattr(row, adapter.attach_column)
        changed = previous != pid
        setattr(row, adapter.attach_column, pid)
        if changed:
            await self._mark_changed(db, adapter, row, owner)
        return {
            'linked': True,
            'changed': changed,
            'resource_uri': resource_uri,
            'project_id': str(pid),
            'previous_project_id': str(previous) if previous is not None else None,
        }

    async def unlink(
        self,
        db: AsyncSession,
        *,
        owner: str,
        resource_uri: str,
        project_id: str | UUID | None = None,
    ) -> dict:
        """把资源从项目摘出；显式给项目时拒绝误摘其它项目，已摘除重放保持幂等。"""
        domain, server_id = parse_hasn_uri(resource_uri)
        adapter = self.get(domain)
        if adapter is None:
            raise _err('unsupported_link_domain', f'资源域「{domain}」暂不支持挂靠进项目')
        row = await self._locate(db, adapter, owner, server_id)
        previous = getattr(row, adapter.attach_column)
        expected = (
            project_id
            if project_id is None or isinstance(project_id, UUID)
            else UUID(str(project_id))
        )
        if previous is not None and expected is not None and previous != expected:
            raise errors.ConflictError(
                msg='资源当前不属于指定项目，拒绝从错误项目摘除',
                data={
                    'error_code': 'resource_project_mismatch',
                    'current_project_id': str(previous),
                    'expected_project_id': str(expected),
                },
            )
        changed = previous is not None
        setattr(row, adapter.attach_column, None)
        if changed:
            await self._mark_changed(db, adapter, row, owner)
        return {
            'unlinked': True,
            'changed': changed,
            'resource_uri': resource_uri,
            'previous_project_id': str(previous) if previous is not None else None,
        }

    @staticmethod
    async def _mark_changed(db: AsyncSession, adapter: LinkageAdapter, row: Any, owner: str) -> None:
        """递增容器版本、flush，并按 adapter 声明推 owner 定向失效信号。"""
        if adapter.revision_column is not None:
            revision = int(getattr(row, adapter.revision_column) or 0)
            setattr(row, adapter.revision_column, revision + 1)
        await db.flush()
        if adapter.sync_kind is not None:
            from backend.app.hasn.service import sync_invalidate_service as siv

            await siv.bump_owner(adapter.sync_kind, db, owner)


project_linkage_registry = ProjectLinkageRegistry()

# ── 平台内建挂靠点：artifact 级（hasn_artifacts.project_id）────────────────────────
# 产物按 `hasn://artifact/{artifact_id}` 显式改挂/摘除（与 register-on-write 自动打标「只进不退」互补）。
# 容器级 adapter 由各应用自己的注册模块按 `platform_project_id` 渐进注册。
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
