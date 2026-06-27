"""短视频（reel）项目化创作 owner 隔离门面（设计 doc29）。

定位与 studio（doc22）的关键区别——**reel 是 downloadable_local（引擎本地 sidecar），不是 cloud-brokered**：
- 引擎（MoneyPrinterTurbo 流水线）跑在**本地 daemon 侧**，云端**不调引擎**；
- 云端只做**权威数据**（项目 / 创作 / 进度 / 产物引用）的 owner 行级隔离 CRUD + 接收 daemon 同步的进度与产物；
- 故本 service **无引擎 broker、无成品物化**（那些是 daemon 的职责）——比 studio_service 薄。

本质（doc29 §0）：一次创作 = 一条 `reel_creation`，归属项目、有进度（透传本地 MPT 流水线或分身工作会话推进）、
有产物（成片 + 中间产物引用）、可回看。三种发起方式（user_pipeline / agent_pipeline / agent_tools）统一落
`reel_creation.kind`。

进度透明（doc29 §3，黑盒→透明的数据层落点）：daemon 把本地引擎/会话推进经 `sync_creation` 写回
`reel_creation.{stage,progress,status,...}`，webui 据此显示进度环 + 阶段（照搬 studio 已验证链路）。

归属：分身建带归属资源时 `agent_hasn_id` 取**凭证身份**（handler 注入），`owner_hasn_id` 行级隔离键。
资产硬约束：库内 `*_asset_uri` 只存 `hasn://asset/`，绝不存 CDN 直链；成片本地优先（doc19 N8），
`video_ref` 存本地引用，显式上云才变 `hasn://asset/`。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from backend.app.hasn_reel.model import ReelCreation, ReelProject
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 创作发起方式（doc29 §1）。
_KINDS = ('user_pipeline', 'agent_pipeline', 'agent_tools')
# 创作状态机（doc29 §2）。
_CREATION_STATUS = ('pending', 'running', 'waiting_user', 'succeeded', 'failed')
_TERMINAL_STATUS = ('succeeded', 'failed')
# 项目状态。
_PROJECT_STATUS = ('active', 'archived')


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReelService:
    """短视频项目化创作 owner 隔离编排（纯数据层：引擎在本地 daemon 侧，本 service 不碰引擎）。"""

    # ================================================================ 项目 CRUD（owner 隔离）

    @staticmethod
    async def save_project(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str | None = None,
        project_id: int | None = None,
        title: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
        cover_asset_uri: str | None = None,
        bound_agent_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """新建或更新短视频项目（owner 隔离）。传 project_id 则更新；新建必带 title。"""
        if status is not None and status not in _PROJECT_STATUS:
            raise errors.RequestError(msg='非法项目状态')

        if project_id is not None:
            row = await ReelService._load_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
            if title is not None:
                row.title = title
            if description is not None:
                row.description = description
            if settings is not None:
                row.settings = dict(settings)
            if cover_asset_uri is not None:
                row.cover_asset_uri = cover_asset_uri
            if bound_agent_id is not None:
                row.bound_agent_id = bound_agent_id
            if status is not None:
                row.status = status
            await db.flush()
            return _serialize_project(row)

        if not title:
            raise errors.RequestError(msg='项目标题必填')
        row = ReelProject(
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            title=title,
            description=description,
            settings=dict(settings or {}),
            cover_asset_uri=cover_asset_uri,
            bound_agent_id=bound_agent_id,
            status=status or 'active',
        )
        db.add(row)
        await db.flush()
        return _serialize_project(row)

    @staticmethod
    async def list_projects(db: AsyncSession, *, owner_hasn_id: str, include_archived: bool = False) -> list[dict[str, Any]]:
        """列出某主人全部项目（行级隔离，最近优先；默认只 active）。"""
        conds = [ReelProject.owner_hasn_id == owner_hasn_id]
        if not include_archived:
            conds.append(ReelProject.status == 'active')
        stmt = select(ReelProject).where(*conds).order_by(ReelProject.id.desc())
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize_project(r) for r in rows]

    @staticmethod
    async def get_project(db: AsyncSession, *, owner_hasn_id: str, project_id: int) -> dict[str, Any]:
        """读项目详情（owner 隔离），含其创作历史列表。"""
        row = await ReelService._load_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
        creations = await ReelService.list_creations(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
        data = _serialize_project(row)
        data['creations'] = creations
        return data

    @staticmethod
    async def delete_project(db: AsyncSession, *, owner_hasn_id: str, project_id: int) -> None:
        """删除项目（owner 隔离）+ 其下全部创作（成片本地优先，本删除只清云端权威元数据）。"""
        row = await ReelService._load_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
        await db.execute(delete(ReelCreation).where(ReelCreation.project_id == row.id))
        await db.delete(row)
        await db.flush()

    # ================================================================ 创作生命周期（owner 隔离）

    @staticmethod
    async def create_creation(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        project_id: int,
        kind: str,
        agent_hasn_id: str | None = None,
        title: str | None = None,
        idea: str | None = None,
        session_id: str | None = None,
        engine_task_id: str | None = None,
    ) -> dict[str, Any]:
        """开一次创作（统一三种发起方式）。校验项目归属（owner 隔离）+ kind 合法。

        起始态 pending（daemon 随后经 sync_creation 推进 running/.../succeeded）。
        ``session_id``（②③ 分身路径工作会话）与 ``engine_task_id``（①② 本地 MPT 任务）可后置（sync 时回填）。
        """
        if kind not in _KINDS:
            raise errors.RequestError(msg='非法创作发起方式')
        # 校验项目存在且属该主人（行级隔离）。
        await ReelService._load_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
        row = ReelCreation(
            project_id=project_id,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            title=title,
            idea=idea,
            kind=kind,
            session_id=session_id,
            engine_task_id=engine_task_id,
            status='pending',
            progress=0,
        )
        db.add(row)
        await db.flush()
        return _serialize_creation(row)

    @staticmethod
    async def list_creations(
        db: AsyncSession, *, owner_hasn_id: str, project_id: int | None = None
    ) -> list[dict[str, Any]]:
        """列创作历史（owner 隔离，可选 project_id 过滤，最近优先）。"""
        conds = [ReelCreation.owner_hasn_id == owner_hasn_id]
        if project_id is not None:
            conds.append(ReelCreation.project_id == project_id)
        stmt = select(ReelCreation).where(*conds).order_by(ReelCreation.id.desc())
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize_creation(r) for r in rows]

    @staticmethod
    async def get_creation(db: AsyncSession, *, owner_hasn_id: str, creation_id: int) -> dict[str, Any]:
        """读一次创作详情（owner 隔离）。"""
        row = await ReelService._load_creation(db, owner_hasn_id=owner_hasn_id, creation_id=creation_id)
        return _serialize_creation(row)

    @staticmethod
    async def sync_creation(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        creation_id: int,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        session_id: str | None = None,
        engine_task_id: str | None = None,
        video_ref: dict[str, Any] | None = None,
        thumbnail_asset_uri: str | None = None,
        duration_sec: float | None = None,
        resolution: str | None = None,
        result_refs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """daemon 同步创作进度/产物回写（doc29 §3 进度透明的数据层落点；owner 隔离）。

        本地引擎/工作会话推进时，daemon 把 stage/progress/status 与（完成时的）成片/产物引用写回这里，
        webui 据此渲染进度环 + 阶段 + 成片。状态机：pending→running→(waiting_user)→succeeded/failed。
        started_at 首次进 running 时落；finished_at 进终态时落。零 fake——error 透传引擎真实错误。
        """
        row = await ReelService._load_creation(db, owner_hasn_id=owner_hasn_id, creation_id=creation_id)

        if status is not None:
            if status not in _CREATION_STATUS:
                raise errors.RequestError(msg='非法创作状态')
            row.status = status
            if status == 'running' and row.started_at is None:
                row.started_at = _now()
            if status in _TERMINAL_STATUS and row.finished_at is None:
                row.finished_at = _now()
        if stage is not None:
            row.stage = stage
        if progress is not None:
            row.progress = max(0, min(100, int(progress)))
        if session_id is not None:
            row.session_id = session_id
        if engine_task_id is not None:
            row.engine_task_id = engine_task_id
        if video_ref is not None:
            row.video_ref = dict(video_ref)
        if thumbnail_asset_uri is not None:
            row.thumbnail_asset_uri = thumbnail_asset_uri
        if duration_sec is not None:
            row.duration_sec = _to_decimal(duration_sec)
        if resolution is not None:
            row.resolution = resolution
        if result_refs is not None:
            row.result_refs = dict(result_refs)
        if error is not None:
            row.error = error

        await db.flush()
        return _serialize_creation(row)

    # ================================================================ 内部加载（owner 隔离）

    @staticmethod
    async def _load_project(db: AsyncSession, *, owner_hasn_id: str, project_id: int) -> ReelProject:
        stmt = select(ReelProject).where(ReelProject.id == project_id, ReelProject.owner_hasn_id == owner_hasn_id)
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='短视频项目不存在')
        return row

    @staticmethod
    async def _load_creation(db: AsyncSession, *, owner_hasn_id: str, creation_id: int) -> ReelCreation:
        stmt = select(ReelCreation).where(
            ReelCreation.id == creation_id, ReelCreation.owner_hasn_id == owner_hasn_id
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='创作不存在')
        return row


# ============================ 序列化 ============================


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _to_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _serialize_project(row: ReelProject) -> dict[str, Any]:
    return {
        'id': row.id,
        'owner_hasn_id': row.owner_hasn_id,
        'agent_hasn_id': row.agent_hasn_id,
        'title': row.title,
        'description': row.description,
        'settings': row.settings or {},
        'cover_asset_uri': row.cover_asset_uri,
        'bound_agent_id': row.bound_agent_id,
        'status': row.status,
        'created_time': _iso(getattr(row, 'created_time', None)),
        'updated_time': _iso(getattr(row, 'updated_time', None)),
    }


def _serialize_creation(row: ReelCreation) -> dict[str, Any]:
    return {
        'id': row.id,
        'project_id': row.project_id,
        'owner_hasn_id': row.owner_hasn_id,
        'agent_hasn_id': row.agent_hasn_id,
        'title': row.title,
        'idea': row.idea,
        'kind': row.kind,
        'session_id': row.session_id,
        'engine_task_id': row.engine_task_id,
        'status': row.status,
        'stage': row.stage,
        'progress': row.progress,
        'video_ref': row.video_ref or None,
        'thumbnail_asset_uri': row.thumbnail_asset_uri,
        'duration_sec': _to_float(row.duration_sec),
        'resolution': row.resolution,
        'result_refs': row.result_refs or {},
        'error': row.error,
        'started_at': _iso(row.started_at),
        'finished_at': _iso(row.finished_at),
        'created_time': _iso(getattr(row, 'created_time', None)),
        'updated_time': _iso(getattr(row, 'updated_time', None)),
    }


reel_service = ReelService()
