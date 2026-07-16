"""平台项目（app_id=project，模块 14 doc38）owner 隔离业务 service（云端权威实现）。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/38-项目管理一级应用(平台项目·联邦挂靠)设计.md §3/§4/§5。

与 plan/designsystem 同范式：codegen 生成的泛型 per-table service/api（keyed on int pk、引用 user_id）
留盘不接线，与本应用 owner_hasn_id/UUID 主键身份模型不兼容；真实业务面由本 service + `hasn.project.*`
云端平台工具 + 自定义 owner API 承载。

owner 隔离铁律（doc38 三铁律之外的隔离约束）：所有读写按 `owner_id`（主人 HASN ID）过滤，
**owner 身份永远由调用方（Owner JWT / Agent JWT claims / MCP Key）解析后传入 `owner=`，绝不接受
请求体携带的身份**。里程碑经父项目归属校验间接落到 owner。项目**不是权限边界**——这两条 scope 只
控「分身能否操作主人的项目容器」，跨 owner 由本 service 的 `owner_id` 过滤兜死。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import sqlalchemy as sa

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn_project.model import HasnProject, HasnProjectMilestone
from backend.app.hasn_project.service.project_linkage_registry import project_linkage_registry
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ── 字段白名单（owner_id/id/时间戳/enterprise_id 永不在内；enterprise_id 服务端注入）──────
_PROJECT_FIELDS = {'name', 'goal', 'cover_asset_uri', 'status', 'bound_agent_id'}
_MILESTONE_FIELDS = {'name', 'due_time', 'status', 'artifact_ref', 'sort'}

# 状态白名单（doc38 §12.3：项目 active/archived；里程碑 pending/done，纯业务态无门控）。
_PROJECT_STATUSES = frozenset({'active', 'archived'})
_MILESTONE_STATUSES = frozenset({'pending', 'done'})

# 时间类字段：Agent/Owner API 的 body 是 untyped dict，时间值经 JSON 必为字符串，
# PostgreSQL 不做 timestamptz=varchar 隐式转换，不转则写入报错。
_DATETIME_KEYS = {'due_time'}


def _err(code: str, msg: str) -> errors.RequestError:
    """业务 400 + 机器可读 error_code（经信封 data 透出，msg 给人话）。"""
    return errors.RequestError(msg=msg, data={'error_code': code})


def _as_uuid(value: Any) -> UUID:
    """入参 id 归一为 UUID（已是 UUID 原样；非法格式如实抛业务 400，不 500）。"""
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError) as e:
        raise _err('invalid_id', '项目/里程碑 id 不是合法 UUID') from e


def _coerce_field(key: str, value: Any) -> Any:
    """时间类字段 ISO 字符串 → datetime（其余原样；非法格式如实抛 ValueError）。"""
    if key in _DATETIME_KEYS and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _pick(fields: set[str], data: dict[str, Any]) -> dict[str, Any]:
    """仅保留白名单字段且值不为 None（None 视为「不设置」，由 DB 默认/原值兜底）。"""
    return {k: _coerce_field(k, v) for k, v in data.items() if k in fields and v is not None}


def serialize(row: Any) -> dict[str, Any]:
    """SQLAlchemy 行 → JSON 安全 dict（UUID→str、datetime→ISO）。

    ⚠️ cover_asset_uri 保留 `hasn://asset/{id}` 原样引用（分身工具面读引用）；owner API 面（U5
    daemon 代理）在序列化边界另经 resolve_assets 换 CDN 签名 URL，不在此处解析（守「不存直链」铁律）。
    """
    out: dict[str, Any] = {}
    # 以 DB 列名作输出键（契约稳定），但用 ORM 属性 key 取值——关键修正：当 DB 列名与 ORM 属性 key
    # 不一致时（如 public.hasn_artifacts 的 `metadata` 列被 declarative 重命名为 `meta_data`，避免撞
    # SQLAlchemy 保留的类级 `metadata`），直接 getattr(row, 列名) 会取到类级 MetaData 对象而非列值，
    # 序列化即炸（产物流并集读命中 HasnArtifacts 时踩到）。故必须经 attr.key 读值。
    mapper = sa.inspect(row).mapper
    for attr in mapper.column_attrs:
        col_name = attr.columns[0].name
        v = getattr(row, attr.key)
        if isinstance(v, UUID):
            out[col_name] = str(v)
        elif isinstance(v, datetime):
            out[col_name] = v.isoformat()
        else:
            out[col_name] = v
    return out


class ProjectService:
    """平台项目 owner 隔离 CRUD + 里程碑管理。所有方法的 owner 由调用方解析后传入。"""

    # ── project ─────────────────────────────────────────────────────────────────
    async def list_projects(self, db: AsyncSession, *, owner: str, status: str | None = None) -> list[dict]:
        """列主人名下项目（默认活跃在前、新建在前）。status 可选过滤 active/archived。"""
        stmt = sa.select(HasnProject).where(HasnProject.owner_id == owner)
        if status:
            stmt = stmt.where(HasnProject.status == status)
        # active 在前、archived 在后；同状态内按创建时间倒序（新项目在上）。
        stmt = stmt.order_by(HasnProject.status.asc(), HasnProject.created_time.desc())
        rows = (await db.execute(stmt)).scalars().all()
        return [serialize(r) for r in rows]

    async def _get_project(self, db: AsyncSession, *, owner: str, pk: str | UUID) -> HasnProject:
        """按 (owner, id) 取项目行；不存在/非本人 → 404（owner 隔离兜死，不泄漏他人项目存在性）。"""
        row = (
            await db.execute(
                sa.select(HasnProject).where(HasnProject.id == _as_uuid(pk), HasnProject.owner_id == owner)
            )
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='项目不存在')
        return row

    async def get_project(self, db: AsyncSession, *, owner: str, pk: str | UUID) -> dict:
        """取单个项目详情（含里程碑轨）。"""
        row = await self._get_project(db, owner=owner, pk=pk)
        data = serialize(row)
        data['milestones'] = await self.list_milestones(db, owner=owner, project_id=row.id)
        return data

    async def assert_owned(self, db: AsyncSession, *, owner: str, pk: str | UUID) -> None:
        """校验项目归属（不存在/非本人 → 404）。link/unlink 前置校验用（挂靠不写他人项目）。"""
        await self._get_project(db, owner=owner, pk=pk)

    async def create_project(
        self, db: AsyncSession, *, owner: str, data: dict, enterprise_id: str | UUID | None = None
    ) -> dict:
        """建项目（name 必填）。enterprise_id 由服务端解析后传入，绝不来自 data。"""
        fields = _pick(_PROJECT_FIELDS, data)
        name = str(fields.get('name') or '').strip()
        if not name:
            raise _err('name_required', '项目名不能为空')
        fields['name'] = name
        status = fields.get('status')
        if status is not None and status not in _PROJECT_STATUSES:
            raise _err('invalid_status', f'项目状态非法（仅 {sorted(_PROJECT_STATUSES)}）')
        row = HasnProject(owner_id=owner, enterprise_id=_as_uuid(enterprise_id) if enterprise_id else None, **fields)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    async def update_project(self, db: AsyncSession, *, owner: str, pk: str | UUID, data: dict) -> dict:
        """改项目（name/goal/封面/状态/绑分身；owner 隔离；空 patch 直接返回原值）。"""
        row = await self._get_project(db, owner=owner, pk=pk)
        fields = _pick(_PROJECT_FIELDS, data)
        status = fields.get('status')
        if status is not None and status not in _PROJECT_STATUSES:
            raise _err('invalid_status', f'项目状态非法（仅 {sorted(_PROJECT_STATUSES)}）')
        for k, v in fields.items():
            setattr(row, k, v)
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    async def archive_project(self, db: AsyncSession, *, owner: str, pk: str | UUID) -> dict:
        """归档项目（status→archived）。归档只是状态标记，不删数据、不断挂靠（doc38：项目非权限边界）。"""
        row = await self._get_project(db, owner=owner, pk=pk)
        row.status = 'archived'
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    # ── milestone（纯业务状态标记·无依赖无门控，doc38 第四铁律）────────────────────────
    async def list_milestones(self, db: AsyncSession, *, owner: str, project_id: str | UUID) -> list[dict]:
        """列某项目里程碑（横向轨次序：sort 升序、到期时间升序）。先校验项目归属。"""
        await self._get_project(db, owner=owner, pk=project_id)
        stmt = (
            sa.select(HasnProjectMilestone)
            .where(HasnProjectMilestone.project_id == _as_uuid(project_id))
            .order_by(HasnProjectMilestone.sort.asc(), HasnProjectMilestone.due_time.asc().nullslast())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [serialize(r) for r in rows]

    async def _get_owned_milestone(
        self, db: AsyncSession, *, owner: str, milestone_id: int
    ) -> HasnProjectMilestone:
        """按 milestone_id 取行并经父项目校验 owner 归属（跨 owner → 404）。"""
        row = (
            await db.execute(
                sa.select(HasnProjectMilestone).where(HasnProjectMilestone.id == int(milestone_id))
            )
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='里程碑不存在')
        # 经父项目归属校验（不存在/非本人即抛 404）。
        await self._get_project(db, owner=owner, pk=row.project_id)
        return row

    async def create_milestone(self, db: AsyncSession, *, owner: str, project_id: str | UUID, data: dict) -> dict:
        """在项目下建里程碑（name 必填）。先校验项目归属。"""
        await self._get_project(db, owner=owner, pk=project_id)
        fields = _pick(_MILESTONE_FIELDS, data)
        name = str(fields.get('name') or '').strip()
        if not name:
            raise _err('name_required', '里程碑名不能为空')
        fields['name'] = name
        status = fields.get('status')
        if status is not None and status not in _MILESTONE_STATUSES:
            raise _err('invalid_status', f'里程碑状态非法（仅 {sorted(_MILESTONE_STATUSES)}）')
        row = HasnProjectMilestone(project_id=_as_uuid(project_id), **fields)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    async def update_milestone(self, db: AsyncSession, *, owner: str, milestone_id: int, data: dict) -> dict:
        """改里程碑（名/到期/状态/关联产物/排序；经父项目校验 owner）。"""
        row = await self._get_owned_milestone(db, owner=owner, milestone_id=milestone_id)
        fields = _pick(_MILESTONE_FIELDS, data)
        status = fields.get('status')
        if status is not None and status not in _MILESTONE_STATUSES:
            raise _err('invalid_status', f'里程碑状态非法（仅 {sorted(_MILESTONE_STATUSES)}）')
        for k, v in fields.items():
            setattr(row, k, v)
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    async def complete_milestone(self, db: AsyncSession, *, owner: str, milestone_id: int) -> dict:
        """完成里程碑（status→done）。纯业务态标记，不触发任何门控/依赖检查（doc38 第四铁律）。"""
        row = await self._get_owned_milestone(db, owner=owner, milestone_id=milestone_id)
        row.status = 'done'
        await db.flush()
        await db.refresh(row)
        return serialize(row)

    # ── 产物流并集读（doc38 §5 item 6）────────────────────────────────────────────
    async def _attached_container_uris(self, db: AsyncSession, *, owner: str, project_id: UUID) -> list[str]:
        """列挂进本项目的容器的 `hasn://` URI（并集读反查用）。

        遍历注册表里的**容器级** adapter（`platform_project_id`）：查 `owner + platform_project_id==pid`
        的容器行，按 `hasn://{domain}/{server_id}` 拼 URI。U3 无容器 adapter → 返 []（机制就位待 U11）。
        """
        uris: list[str] = []
        for adapter in project_linkage_registry.container_adapters():
            id_col = getattr(adapter.model, adapter.id_column)
            owner_col = getattr(adapter.model, adapter.owner_column)
            attach_col = getattr(adapter.model, adapter.attach_column)
            ids = (await db.execute(sa.select(id_col).where(owner_col == owner, attach_col == project_id))).scalars().all()
            uris.extend(f'hasn://{adapter.domain}/{i}' for i in ids)
        return uris

    async def project_artifact_flow(
        self, db: AsyncSession, *, owner: str, project_id: str | UUID, limit: int = 50
    ) -> list[dict]:
        """产物流并集读：`project_id` 直接命中 ∪ 挂靠容器名下产物（读时派生不回填）。

        - **直接命中**：`hasn_artifacts.project_id == pid`（register-on-write 自动打标 / 显式 link）。
        - **容器名下**：经容器 `platform_project_id` 反查其 `hasn://` URI，并入 `resource_uri` 命中的产物。
          U3 无容器 adapter → 并集退化为仅直接命中，但代码路径已就位（U11 注册容器 adapter 即生效）。
        - 读时派生不回填：不把容器名下产物的 `project_id` 写实（doc38 §5/§6）。
        """
        await self._get_project(db, owner=owner, pk=project_id)  # 归属校验（非本人 → 404）
        pid = _as_uuid(project_id)
        union_conds = [HasnArtifacts.project_id == pid]
        container_uris = await self._attached_container_uris(db, owner=owner, project_id=pid)
        if container_uris:
            union_conds.append(HasnArtifacts.resource_uri.in_(container_uris))
        stmt = (
            sa.select(HasnArtifacts)
            .where(
                HasnArtifacts.owner_hasn_id == owner,
                HasnArtifacts.status == 'active',
                sa.or_(*union_conds),
            )
            .order_by(HasnArtifacts.created_time.desc())
            .limit(limit)
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [serialize(r) for r in rows]


project_service = ProjectService()
