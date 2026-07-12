"""统一视频引擎 studio 业务编排（云端唯一 Broker，设计 doc22 §3）。

studio 是 **cloud-brokered** 应用（对齐 creator/finance/quant）：分身经云端 MCP 调 `hasn.studio.*` →
本 service 落产品数据（`hasn_studio` PG，权威）+ 经 `montage_engine_provider` 调引擎服务跑真渲染/出片。

工具线（P3）：
- 读：list_pipelines（broker）/ list_projects / get_project / list_assets / list_artifacts / get_render_job。
- 写：save_project（项目 CRUD）/ save_storyboard（分镜存 project.settings['storyboard']，避免假 asset_uri）。
- 渲染（花算力，ask）：run_pipeline / render（共用 _submit_render，落 studio_render_job + broker /v1/render）/
  run_tool（原子工具透传创作段）。
- 导出（外发，ask）：export（owner 隔离取成品，序列化边界换 CDN 签名 URL）。

成品物化（关键，真实零 fake）：get_render_job 惰性轮询发现引擎 job succeeded 且本 render_job 尚无对应
artifact → broker 取 mp4 字节 → 上传唤星私有桶得 hasn://asset/ → 落 studio_artifact + 回流通用产物索引
public.hasn_artifacts（同引同一 hasn://asset/）。S3/上传不可用时：job 仍 succeeded，artifact 不落 + 透传
真实错误（绝不 fake 一个 artifact）。

归属（PLANFIX-6）：分身建带归属资源时 `agent_hasn_id` 取**凭证身份**（handler 从 Agent JWT 注入），
`owner_hasn_id` 行级隔离键。资产硬约束：库内只存 hasn://asset/，绝不存 CDN 直链；只在 export/list_artifacts
序列化边界换签名 URL。
"""

from __future__ import annotations

import logging

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from backend.app.hasn.schema.hasn_artifacts import RecordArtifactParam
from backend.app.hasn.service.authz import Subject  # G6：收编来源，模块级再导出（既有调用点不变）
from backend.app.hasn.service.hasn_artifacts_service import hasn_artifacts_service
from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn.service.resource_share_service import rank, resource_share_service
from backend.app.hasn_studio.model import StudioArtifact, StudioAsset, StudioProject, StudioRenderJob
from backend.app.hasn_studio.provider import StudioEngineError, montage_engine_provider
from backend.app.hasn_studio.service.media_credentials import resolve_media_credentials
from backend.common.exception import errors
from backend.plugin.s3.service.storage_service import StorageService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# hasn://asset/ URI 前缀（库内只存此引用，序列化边界换 CDN 签名 URL）。
_ASSET_URI_PREFIX = 'hasn://asset/'
# 渲染 job 终态。
_TERMINAL = ('succeeded', 'failed', 'canceled')
# 成品物化用的私有桶上传类别（不触发 extract 抽取流水线；语义=已发布制品/产物字节）。
_ARTIFACT_UPLOAD_CATEGORY = 'published_artifact'

# 产物级协作（应用平台 v3 §6 / doc22 §3.6 全复用）：resource_share 的 resource_type。
# studio 有两类可分享产物：项目（容器：流水线/素材/分镜，editor 可改、可派渲染）+ 成品（最终视频）。
_RESOURCE_TYPE_PROJECT = 'studio_project'
_RESOURCE_TYPE_ARTIFACT = 'studio_artifact'
# studio 项目/成品**无独立 visibility/scope/enterprise 列**（doc22 §3.1 数据模型；不同于知识库 Kb）：
# 分享是「纯显式 ACL」（owner ∪ 经 resource_share 共享）。故有效权限判定的可见性/企业三参恒取保守默认
# （personal / 无企业 / private）——visibility_grant 永不命中，唯 owner_grant + explicit_grant 生效。
_DEFAULT_OWNER_SCOPE = 'personal'
_DEFAULT_VISIBILITY = 'private'
# 协作者档位（与知识库一致：viewer 只读 / editor 可改可派渲染 / manager 可再分享）。
_PERMISSIONS = ('viewer', 'editor', 'manager')
# 协作者类型（与知识库一致；分享给 agent = 能力授权，该分身 hasn.studio.* 可操作此产物）。
_GRANTEE_TYPES = ('human', 'agent', 'enterprise')


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _asset_uri(asset_id: str) -> str:
    return f'{_ASSET_URI_PREFIX}{asset_id}'


def _asset_id_from_uri(uri: str | None) -> str | None:
    """从 hasn://asset/<id> 抽 asset_id；非该形态返回 None（不臆造）。"""
    if uri and uri.startswith(_ASSET_URI_PREFIX):
        aid = uri[len(_ASSET_URI_PREFIX) :].strip()
        return aid or None
    return None


class StudioService:
    """统一视频引擎业务线编排（owner 隔离 + 引擎 broker + 成品物化）。无 mock：渲染真打引擎、成品真落库。"""

    # ================================================================ 产物级协作：权限闸（doc22 §3.6 全复用）
    #
    # 复用纪律（对齐 knowledge）：现有 owner_hasn_id keyed 方法（项目/素材/成品/渲染读写）一字不动——
    # 「先 authorize 闸判权，再用 row.owner_hasn_id 委托旧方法」对产物主人与被分享者都返回正确结果，
    # 零重写、零回归。被分享 editor 新建/派渲染的 job/artifact 仍随产物 owner_hasn_id 归属（行级隔离键）。
    #
    # studio 项目/成品**无 visibility/scope/enterprise 列**（doc22 §3.1）→ 分享 = 纯显式 ACL，
    # 有效权限 = max(owner_grant, explicit_grant)；可见性/企业三参恒传保守默认（见模块常量）。

    @staticmethod
    async def _load_project_unconstrained(db: AsyncSession, project_id: int) -> StudioProject:
        """按 id 取项目（不做 owner 过滤；access 交给权限闸）。"""
        row = (await db.execute(select(StudioProject).where(StudioProject.id == project_id))).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='视频项目不存在')
        return row

    @staticmethod
    async def _load_artifact_unconstrained(db: AsyncSession, artifact_id: int) -> StudioArtifact:
        """按 id 取成品（不做 owner 过滤；access 交给权限闸）。"""
        row = (await db.execute(select(StudioArtifact).where(StudioArtifact.id == artifact_id))).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='成品不存在')
        return row

    @staticmethod
    async def _effective_project_permission(db: AsyncSession, *, project: StudioProject, subject: Subject) -> str:
        return await resource_share_service.resolve_effective_permission(
            db,
            subject_hasn_id=subject.hasn_id,
            subject_kind=subject.kind,
            subject_owner_hasn_id=subject.owner_hasn_id,
            resource_type=_RESOURCE_TYPE_PROJECT,
            resource_id=str(project.id),
            resource_owner_hasn_id=project.owner_hasn_id,
            resource_owner_scope=_DEFAULT_OWNER_SCOPE,
            resource_enterprise_id=None,
            resource_visibility=_DEFAULT_VISIBILITY,
        )

    @staticmethod
    async def _effective_artifact_permission(db: AsyncSession, *, artifact: StudioArtifact, subject: Subject) -> str:
        return await resource_share_service.resolve_effective_permission(
            db,
            subject_hasn_id=subject.hasn_id,
            subject_kind=subject.kind,
            subject_owner_hasn_id=subject.owner_hasn_id,
            resource_type=_RESOURCE_TYPE_ARTIFACT,
            resource_id=str(artifact.id),
            resource_owner_hasn_id=artifact.owner_hasn_id,
            resource_owner_scope=_DEFAULT_OWNER_SCOPE,
            resource_enterprise_id=None,
            resource_visibility=_DEFAULT_VISIBILITY,
        )

    @staticmethod
    async def authorize_project(db: AsyncSession, *, subject: Subject, project_id: int, need: str) -> StudioProject:
        """校验 subject 对项目至少有 need 权限。不足报错（none→不存在不泄露，其它→无权限）。返回项目行
        （caller 用 row.owner_hasn_id 委托旧 owner-keyed 方法）。"""
        project = await StudioService._load_project_unconstrained(db, project_id)
        eff = await StudioService._effective_project_permission(db, project=project, subject=subject)
        if rank(eff) < rank(need):
            if rank(eff) == 0:
                raise errors.NotFoundError(msg='视频项目不存在')
            raise errors.ForbiddenError(msg='没有该操作权限')
        return project

    @staticmethod
    async def authorize_artifact(db: AsyncSession, *, subject: Subject, artifact_id: int, need: str) -> StudioArtifact:
        """校验 subject 对成品至少有 need 权限。不足报错。返回成品行（caller 用 row.owner_hasn_id 委托旧方法）。"""
        artifact = await StudioService._load_artifact_unconstrained(db, artifact_id)
        eff = await StudioService._effective_artifact_permission(db, artifact=artifact, subject=subject)
        if rank(eff) < rank(need):
            if rank(eff) == 0:
                raise errors.NotFoundError(msg='成品不存在')
            raise errors.ForbiddenError(msg='没有该操作权限')
        return artifact

    @staticmethod
    async def _shared_ids_for_agent(db: AsyncSession, *, resource_type: str, agent_hasn_id: str) -> set[str]:
        """某分身经「显式共享给该 agent」能看到的产物 id 集合。"""
        from backend.app.hasn.model import HasnResourceShare

        rows = (
            (
                await db.execute(
                    select(HasnResourceShare.resource_id).where(
                        HasnResourceShare.resource_type == resource_type,
                        HasnResourceShare.status == 'active',
                        HasnResourceShare.grantee_type == 'agent',
                        HasnResourceShare.grantee_id == agent_hasn_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return set(rows)

    @staticmethod
    async def list_accessible_projects(db: AsyncSession, *, subject: Subject) -> list[dict[str, Any]]:
        """可访问视频项目 = 我拥有的 ∪ 共享给我的，每条带 relation('owner'|'shared') + my_permission。"""
        human = subject.owner_hasn_id
        owned = (
            (
                await db.execute(
                    select(StudioProject).where(StudioProject.owner_hasn_id == human).order_by(StudioProject.id.desc())
                )
            )
            .scalars()
            .all()
        )
        owned_ids = {p.id for p in owned}

        shared_ids: set[str] = set(
            await resource_share_service.shared_resource_ids_for_human(
                db, resource_type=_RESOURCE_TYPE_PROJECT, human_hasn_id=human
            )
        )
        if subject.kind == 'agent':
            shared_ids |= await StudioService._shared_ids_for_agent(
                db, resource_type=_RESOURCE_TYPE_PROJECT, agent_hasn_id=subject.hasn_id
            )
        extra_ids = {int(i) for i in shared_ids if i.isdigit()} - owned_ids
        extra: list[StudioProject] = []
        if extra_ids:
            extra = list(
                (await db.execute(select(StudioProject).where(StudioProject.id.in_(extra_ids)))).scalars().all()
            )

        items: list[dict[str, Any]] = [_serialize_project(p, my_permission='manager', relation='owner') for p in owned]
        for p in extra:
            eff = await StudioService._effective_project_permission(db, project=p, subject=subject)
            if rank(eff) == 0:
                continue
            items.append(_serialize_project(p, my_permission=eff, relation='shared'))
        return items

    @staticmethod
    async def list_accessible_artifacts(
        db: AsyncSession, *, subject: Subject, project_id: int | None = None
    ) -> list[dict[str, Any]]:
        """可访问成品 = 我拥有的 ∪ 共享给我的，每条带 relation + my_permission，序列化边界换签名 URL。

        签名 URL 用各成品 **owner_hasn_id** 解析（owner 恒可读自己资产；被分享者看到的是产物主人的资产）。
        可选 project_id 过滤（owned 与 shared 都收敛到该项目）。
        """
        human = subject.owner_hasn_id
        owned_conds = [StudioArtifact.owner_hasn_id == human]
        if project_id is not None:
            owned_conds.append(StudioArtifact.project_id == project_id)
        owned = (
            (await db.execute(select(StudioArtifact).where(*owned_conds).order_by(StudioArtifact.id.desc())))
            .scalars()
            .all()
        )
        owned_ids = {a.id for a in owned}

        shared_ids: set[str] = set(
            await resource_share_service.shared_resource_ids_for_human(
                db, resource_type=_RESOURCE_TYPE_ARTIFACT, human_hasn_id=human
            )
        )
        if subject.kind == 'agent':
            shared_ids |= await StudioService._shared_ids_for_agent(
                db, resource_type=_RESOURCE_TYPE_ARTIFACT, agent_hasn_id=subject.hasn_id
            )
        extra_ids = {int(i) for i in shared_ids if i.isdigit()} - owned_ids
        extra: list[StudioArtifact] = []
        if extra_ids:
            extra_conds = [StudioArtifact.id.in_(extra_ids)]
            if project_id is not None:
                extra_conds.append(StudioArtifact.project_id == project_id)
            extra = list((await db.execute(select(StudioArtifact).where(*extra_conds))).scalars().all())

        rows: list[tuple[StudioArtifact, str, str]] = [(a, 'manager', 'owner') for a in owned]
        for a in extra:
            eff = await StudioService._effective_artifact_permission(db, artifact=a, subject=subject)
            if rank(eff) == 0:
                continue
            rows.append((a, eff, 'shared'))

        # 按各成品自己的 owner 分组批量换签名 URL（不同 owner 的资产分别解析，避免越权读不到）。
        url_map: dict[str, str] = {}
        owner_to_uris: dict[str, list[str | None]] = {}
        for a, _, _ in rows:
            owner_to_uris.setdefault(a.owner_hasn_id, []).extend([a.video_asset_uri, a.thumbnail_asset_uri])
        for owner, uris in owner_to_uris.items():
            url_map.update(await StudioService._resolve_asset_urls(db, owner_hasn_id=owner, uris=uris))
        return [_serialize_artifact(a, url_map, my_permission=perm, relation=rel) for a, perm, rel in rows]

    # ---------------- 共享名单管理（项目；manager 权） ----------------

    @staticmethod
    async def list_project_shares(db: AsyncSession, *, subject: Subject, project_id: int) -> dict[str, Any]:
        await StudioService.authorize_project(db, subject=subject, project_id=project_id, need='manager')
        shares = await resource_share_service.list_shares(
            db, resource_type=_RESOURCE_TYPE_PROJECT, resource_id=str(project_id)
        )
        return {'project_id': project_id, 'shares': shares}

    @staticmethod
    async def add_project_share(
        db: AsyncSession,
        *,
        subject: Subject,
        project_id: int,
        grantee_type: str,
        grantee_id: str,
        permission: str,
    ) -> dict[str, Any]:
        """加/改一条项目协作授权（grantee=agent 即能力授权：该分身 hasn.studio.* 可操作此项目）。"""
        StudioService._validate_share_input(grantee_type, permission)
        project = await StudioService.authorize_project(db, subject=subject, project_id=project_id, need='manager')
        return await resource_share_service.upsert_share(
            db,
            resource_type=_RESOURCE_TYPE_PROJECT,
            resource_id=str(project_id),
            owner_hasn_id=project.owner_hasn_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission=permission,
            granted_by=subject.hasn_id,
        )

    @staticmethod
    async def revoke_project_share(
        db: AsyncSession, *, subject: Subject, project_id: int, grantee_type: str, grantee_id: str
    ) -> bool:
        await StudioService.authorize_project(db, subject=subject, project_id=project_id, need='manager')
        return await resource_share_service.revoke_share(
            db,
            resource_type=_RESOURCE_TYPE_PROJECT,
            resource_id=str(project_id),
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )

    # ---------------- 共享名单管理（成品；manager 权） ----------------

    @staticmethod
    async def list_artifact_shares(db: AsyncSession, *, subject: Subject, artifact_id: int) -> dict[str, Any]:
        await StudioService.authorize_artifact(db, subject=subject, artifact_id=artifact_id, need='manager')
        shares = await resource_share_service.list_shares(
            db, resource_type=_RESOURCE_TYPE_ARTIFACT, resource_id=str(artifact_id)
        )
        return {'artifact_id': artifact_id, 'shares': shares}

    @staticmethod
    async def add_artifact_share(
        db: AsyncSession,
        *,
        subject: Subject,
        artifact_id: int,
        grantee_type: str,
        grantee_id: str,
        permission: str,
    ) -> dict[str, Any]:
        """加/改一条成品协作授权（grantee=agent 即能力授权：该分身 hasn.studio.* 可操作此成品）。"""
        StudioService._validate_share_input(grantee_type, permission)
        artifact = await StudioService.authorize_artifact(db, subject=subject, artifact_id=artifact_id, need='manager')
        return await resource_share_service.upsert_share(
            db,
            resource_type=_RESOURCE_TYPE_ARTIFACT,
            resource_id=str(artifact_id),
            owner_hasn_id=artifact.owner_hasn_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission=permission,
            granted_by=subject.hasn_id,
        )

    @staticmethod
    async def revoke_artifact_share(
        db: AsyncSession, *, subject: Subject, artifact_id: int, grantee_type: str, grantee_id: str
    ) -> bool:
        await StudioService.authorize_artifact(db, subject=subject, artifact_id=artifact_id, need='manager')
        return await resource_share_service.revoke_share(
            db,
            resource_type=_RESOURCE_TYPE_ARTIFACT,
            resource_id=str(artifact_id),
            grantee_type=grantee_type,
            grantee_id=grantee_id,
        )

    @staticmethod
    def _validate_share_input(grantee_type: str, permission: str) -> None:
        if grantee_type not in _GRANTEE_TYPES:
            raise errors.RequestError(msg='仅支持 human/agent/enterprise 协作者')
        if permission not in _PERMISSIONS:
            raise errors.RequestError(msg='非法权限档')

    # ================================================================ 对外公开发布（M18 web 发布全复用）
    #
    # doc22 §3.6 / §9 S18：成片对外公开走模块 18（hasn_publish）——零新表零新发布逻辑。
    # 复用 PublishService.create_site/update_site/get_by_source（kind='video'，source_app='studio'，
    # source_ref=<artifact_id>）。**关键（资产纪律）**：revision.asset_id 直接存成片的 hasn_assets id +
    # runtime='video-landing' → /s/{slug} 渲染时**才**用该 asset 现解析签名 URL 生成 <video> 落地页
    # （绝不在发布期把签名 URL 烤进静态 HTML，对齐「signed URL 只在序列化/serve 边界」）。

    @staticmethod
    async def publish_artifact(
        db: AsyncSession,
        *,
        subject: Subject,
        artifact_id: int,
        title: str | None = None,
        visibility: str = 'unlisted',
        password: str | None = None,
        allow_download: bool = False,
    ) -> dict[str, Any]:
        """把一个成片对外发布为可分享网页（需 manager 权）。已发布过则更新现有 Site（slug/URL 不变）。

        返回 publish service 的 {site, revision}（site.slug 即 /s/{slug} 路径）。
        """
        from backend.app.hasn_publish.service.publish_service import publish_service

        artifact = await StudioService.authorize_artifact(db, subject=subject, artifact_id=artifact_id, need='manager')
        asset_id = _asset_id_from_uri(artifact.video_asset_uri)
        if not asset_id:
            raise errors.RequestError(msg='该成品暂无可发布的成片资产')
        pub_title = (title or artifact.title or '视频').strip()[:200] or '视频'
        manifest = {'media_kind': 'video', 'resolution': artifact.resolution}

        existing = await publish_service.get_by_source(
            db, owner_id=artifact.owner_hasn_id, source_app='studio', source_ref=str(artifact_id)
        )
        if existing is not None:
            # 已发布过 → 更新内容（新 revision，slug 不变）+ 同步可见性/口令/下载能力。
            result = await publish_service.update_site(
                db,
                owner_id=artifact.owner_hasn_id,
                site_id=existing['id'],
                asset_id=asset_id,
                runtime='video-landing',
                content_hash='',
                size_bytes=0,
                manifest_json=manifest,
            )
            site = await publish_service.set_visibility(
                db,
                owner_id=artifact.owner_hasn_id,
                site_id=existing['id'],
                visibility=visibility,
                password=password,
                allow_download=allow_download,
            )
            result['site'] = site
            return result

        return await publish_service.create_site(
            db,
            owner_id=artifact.owner_hasn_id,
            publisher_agent_id=subject.hasn_id if subject.kind == 'agent' else None,
            kind='video',
            title=pub_title,
            asset_id=asset_id,
            runtime='video-landing',
            content_hash='',
            size_bytes=0,
            manifest_json=manifest,
            visibility=visibility,
            password=password,
            allow_present=False,  # 视频落地页无放映态
            allow_download=allow_download,
            source_app='studio',
            source_ref=str(artifact_id),
        )

    @staticmethod
    async def get_artifact_publication(
        db: AsyncSession, *, subject: Subject, artifact_id: int
    ) -> dict[str, Any] | None:
        """读取某成片的对外发布态（需 viewer 权；未发布返回 None）。"""
        from backend.app.hasn_publish.service.publish_service import publish_service

        artifact = await StudioService.authorize_artifact(db, subject=subject, artifact_id=artifact_id, need='viewer')
        return await publish_service.get_by_source(
            db, owner_id=artifact.owner_hasn_id, source_app='studio', source_ref=str(artifact_id)
        )

    @staticmethod
    async def unpublish_artifact(db: AsyncSession, *, subject: Subject, artifact_id: int) -> bool:
        """撤销成片对外发布（需 manager 权；URL 返回 410）。返回是否命中已发布 Site。"""
        from backend.app.hasn_publish.service.publish_service import publish_service

        artifact = await StudioService.authorize_artifact(db, subject=subject, artifact_id=artifact_id, need='manager')
        existing = await publish_service.get_by_source(
            db, owner_id=artifact.owner_hasn_id, source_app='studio', source_ref=str(artifact_id)
        )
        if existing is None:
            return False
        await publish_service.revoke(db, owner_id=artifact.owner_hasn_id, site_id=existing['id'])
        return True

    # ================================================================ 流水线目录（broker）

    @staticmethod
    async def list_pipelines() -> dict[str, Any]:
        """经 broker 取引擎流水线目录（只 production）。传输/业务失败抛 StudioEngineError（handler/上层处理）。"""
        data = await montage_engine_provider.list_pipelines()
        pipelines = data.get('pipelines') or []
        return {'pipelines': pipelines, 'count': data.get('count', len(pipelines))}

    # ================================================================ 项目 CRUD

    @staticmethod
    async def save_project(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str | None = None,
        project_id: int | None = None,
        title: str | None = None,
        description: str | None = None,
        default_pipeline_key: str | None = None,
        settings: dict[str, Any] | None = None,
        cover_asset_uri: str | None = None,
        bound_agent_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """新建或更新视频项目（owner 隔离）。传 project_id 则更新；新建必带 title。"""
        if project_id is not None:
            row = await StudioService._load_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
            if title is not None:
                row.title = title
            if description is not None:
                row.description = description
            if default_pipeline_key is not None:
                row.default_pipeline_key = default_pipeline_key
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
        row = StudioProject(
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            title=title,
            description=description,
            default_pipeline_key=default_pipeline_key,
            settings=dict(settings or {}),
            cover_asset_uri=cover_asset_uri,
            bound_agent_id=bound_agent_id,
            status=status or 'draft',
        )
        db.add(row)
        await db.flush()
        return _serialize_project(row)

    @staticmethod
    async def list_projects(db: AsyncSession, *, owner_hasn_id: str) -> list[dict[str, Any]]:
        """列出某主人全部项目（行级隔离，最近优先）。"""
        stmt = (
            select(StudioProject).where(StudioProject.owner_hasn_id == owner_hasn_id).order_by(StudioProject.id.desc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize_project(r) for r in rows]

    @staticmethod
    async def get_project(db: AsyncSession, *, owner_hasn_id: str, project_id: int) -> dict[str, Any]:
        """读项目详情（owner 隔离），含其素材列表。"""
        row = await StudioService._load_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
        assets = await StudioService.list_assets(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
        data = _serialize_project(row)
        data['assets'] = assets
        return data

    @staticmethod
    async def save_storyboard(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        project_id: int,
        storyboard: Any,
    ) -> dict[str, Any]:
        """承载分镜脚本。**实现为更新 studio_project.settings['storyboard']**（避免假 asset_uri）。

        不新建 studio_asset（脚本无独立资产本体；存为内联文本/JSON 进项目设置，喂 run_pipeline 缺省）。
        返回更新后的项目（含 settings.storyboard）。
        """
        row = await StudioService._load_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
        new_settings = dict(row.settings or {})
        new_settings['storyboard'] = storyboard
        row.settings = new_settings
        await db.flush()
        return _serialize_project(row)

    # ================================================================ 素材 / 成品 读

    @staticmethod
    async def list_assets(db: AsyncSession, *, owner_hasn_id: str, project_id: int) -> list[dict[str, Any]]:
        """按 project_id 列素材（owner 隔离）。"""
        stmt = (
            select(StudioAsset)
            .where(StudioAsset.owner_hasn_id == owner_hasn_id, StudioAsset.project_id == project_id)
            .order_by(StudioAsset.id.desc())
        )
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize_asset(r) for r in rows]

    @staticmethod
    async def list_artifacts(
        db: AsyncSession, *, owner_hasn_id: str, project_id: int | None = None
    ) -> list[dict[str, Any]]:
        """列成品（owner 隔离，可选 project_id 过滤，最近优先）。

        序列化边界对 *_asset_uri 换 owner 可读的签名 URL（owner 恒可读自己资产）。
        """
        conds = [StudioArtifact.owner_hasn_id == owner_hasn_id]
        if project_id is not None:
            conds.append(StudioArtifact.project_id == project_id)
        stmt = select(StudioArtifact).where(*conds).order_by(StudioArtifact.id.desc())
        rows = (await db.execute(stmt)).scalars().all()
        url_map = await StudioService._resolve_asset_urls(
            db,
            owner_hasn_id=owner_hasn_id,
            uris=[r.video_asset_uri for r in rows] + [r.thumbnail_asset_uri for r in rows],
        )
        return [_serialize_artifact(r, url_map) for r in rows]

    @staticmethod
    async def export(
        db: AsyncSession, *, owner_hasn_id: str, artifact_id: int, fmt: str | None = None
    ) -> dict[str, Any]:
        """导出成片（owner 隔离）：经序列化边界换 CDN 签名 URL，返回下载信息。

        format 参数当前仅记录（引擎成片即 mp4，转码非 P3 范围）；零 fake——asset 解析不到则无 download_url。
        """
        row = await StudioService._load_artifact(db, owner_hasn_id=owner_hasn_id, artifact_id=artifact_id)
        url_map = await StudioService._resolve_asset_urls(db, owner_hasn_id=owner_hasn_id, uris=[row.video_asset_uri])
        download_url = url_map.get(row.video_asset_uri)
        return {
            'artifact_id': row.id,
            'title': row.title,
            'video_asset_uri': row.video_asset_uri,
            'download_url': download_url,
            'duration_sec': _to_float(row.duration_sec),
            'resolution': row.resolution,
            'format': fmt or 'mp4',
        }

    # ================================================================ 渲染线（job 式）

    @staticmethod
    async def run_pipeline(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str | None = None,
        project_id: int,
        pipeline_key: str | None = None,
        input_payload: dict[str, Any] | None = None,
        work_session_id: str | None = None,
    ) -> dict[str, Any]:
        """复合主入口：按 (project_id, pipeline_key, input) 落 render_job + broker /v1/render，立即返回 job ref。

        pipeline_key 缺省回落项目 default_pipeline_key；input 里取 props/demo/composition_id 喂引擎。
        引擎传输/业务失败 → job.status=failed + 透传真实 error（零 fake）。
        """
        project = await StudioService._load_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
        eff_pipeline = pipeline_key or project.default_pipeline_key or ''
        return await StudioService._submit_render(
            db,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            project_id=project_id,
            pipeline_key=eff_pipeline,
            input_payload=dict(input_payload or {}),
            work_session_id=work_session_id,
        )

    @staticmethod
    async def render(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str | None = None,
        project_id: int,
        props: dict[str, Any] | None = None,
        demo: str | None = None,
        pipeline_key: str | None = None,
        composition_id: str | None = None,
        work_session_id: str | None = None,
    ) -> dict[str, Any]:
        """较低层直渲染：(project_id, props?|demo?, composition_id?, pipeline_key?) → 同样落 render_job + broker。

        ``demo`` 是引擎内置 demo-props 名（字符串，如 'world-in-numbers'），不是布尔；与 props 二选一。
        """
        project = await StudioService._load_project(db, owner_hasn_id=owner_hasn_id, project_id=project_id)
        eff_pipeline = pipeline_key or project.default_pipeline_key or ''
        input_payload: dict[str, Any] = {}
        if props is not None:
            input_payload['props'] = props
        if demo is not None:
            input_payload['demo'] = demo
        if composition_id is not None:
            input_payload['composition_id'] = composition_id
        return await StudioService._submit_render(
            db,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            project_id=project_id,
            pipeline_key=eff_pipeline,
            input_payload=input_payload,
            work_session_id=work_session_id,
        )

    @staticmethod
    async def _submit_render(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str | None,
        project_id: int,
        pipeline_key: str,
        input_payload: dict[str, Any],
        work_session_id: str | None,
    ) -> dict[str, Any]:
        """run_pipeline / render 共用：落 render_job(queued) → broker /v1/render → 记 engine_job_id/status。"""
        job = StudioRenderJob(
            project_id=project_id,
            owner_hasn_id=owner_hasn_id,
            agent_hasn_id=agent_hasn_id,
            pipeline_key=pipeline_key,
            input=dict(input_payload),  # 入参快照（不回指项目当前值）
            status='queued',
            progress=0,
            work_session_id=work_session_id,
        )
        # 按主人身份解析临时媒体凭据 ENV 覆盖表（doc22 §5 P7）：网关族用主人 new-api token（OWNER 配额计费）、
        # 长尾族用主人 BYO / 平台兜底。引擎瞬时套用、绝不持久；解析失败保守（缺则省略，零 fake）。
        credentials = await resolve_media_credentials(db, owner_hasn_id=owner_hasn_id)
        try:
            snapshot = await montage_engine_provider.submit_render(
                props=input_payload.get('props'),
                demo=input_payload.get('demo'),
                pipeline_key=pipeline_key or None,
                composition_id=input_payload.get('composition_id'),
                credentials=credentials,
            )
            job.engine_job_id = snapshot.get('job_id')
            job.status = _coerce_status(snapshot.get('status'), default='running')
            job.progress = int(snapshot.get('progress') or 0)
            job.stage = snapshot.get('stage')
            job.started_at = _now()
        except StudioEngineError as exc:
            job.status = 'failed'
            job.error = str(exc)
            job.finished_at = _now()
        db.add(job)
        await db.flush()
        return _serialize_render_job(job)

    # ================================================================ 原子工具透传（创作段）

    @staticmethod
    async def run_tool(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        agent_hasn_id: str | None = None,
        tool_name: str,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """原子工具透传（创作段）：broker POST /v1/tools/{tool_name}，返回 {result}。

        owner_hasn_id/agent_hasn_id 仅用于审计上下文（引擎无产品身份）；不落库（创作段中间产物由引擎管）。
        传输/业务失败抛 StudioEngineError（含未知工具 404）。
        """
        if not tool_name:
            raise errors.RequestError(msg='tool_name 必填')
        # 原子工具创作段同样按主人身份下发临时媒体凭据（doc22 §5 P7）：引擎只套用该工具读的 ENV。
        credentials = await resolve_media_credentials(db, owner_hasn_id=owner_hasn_id)
        return await montage_engine_provider.run_tool(tool_name, dict(inputs or {}), credentials=credentials)

    # ================================================================ 渲染 job 读 + 成品物化

    @staticmethod
    async def get_render_job(db: AsyncSession, *, owner_hasn_id: str, render_job_id: int) -> dict[str, Any]:
        """读渲染 job（owner 隔离）。非终态且有 engine_job_id → 惰性轮询引擎落 status/progress/stage/cost。

        成功首次轮询到（且本 job 尚无 artifact）→ 物化成品（取片 + 上传私有桶 + 落 studio_artifact +
        回流 hasn_artifacts）。物化失败：job 仍 succeeded，artifact 不落 + 记录真实错误（零 fake）。
        """
        job = await StudioService._load_render_job(db, owner_hasn_id=owner_hasn_id, render_job_id=render_job_id)
        if job.status not in _TERMINAL and job.engine_job_id:
            await StudioService._poll_and_apply(db, job)
        if job.status == 'succeeded' and job.engine_job_id:
            await StudioService._ensure_artifact(db, job)
        return _serialize_render_job(job)

    @staticmethod
    async def _poll_and_apply(db: AsyncSession, job: StudioRenderJob) -> None:
        """轮询引擎一次，把状态/进度/阶段/成本落库。传输层失败保持原态（下次重试），不造假。"""
        try:
            snapshot = await montage_engine_provider.get_render(job.engine_job_id)
        except StudioEngineError as exc:
            # 404=引擎重启丢内存态 → 标失败透传；其余瞬时错误保持原态等下次。
            if 'HTTP 404' in str(exc):
                job.status = 'failed'
                job.error = f'引擎侧 job 已不存在: {exc}'
                job.finished_at = _now()
                await db.flush()
            return
        status = _coerce_status(snapshot.get('status'), default=job.status)
        job.status = status
        job.progress = int(snapshot.get('progress') or job.progress or 0)
        job.stage = snapshot.get('stage') or job.stage
        if snapshot.get('error'):
            job.error = str(snapshot.get('error'))
        cost = _build_cost(snapshot)
        if cost:
            job.cost = cost
        if status == 'running' and job.started_at is None:
            job.started_at = _now()
        if status in _TERMINAL and job.finished_at is None:
            job.finished_at = _now()
        await db.flush()

    @staticmethod
    async def _ensure_artifact(db: AsyncSession, job: StudioRenderJob) -> None:
        """job succeeded 且尚无对应 artifact → 物化成品（真实，零 fake）。

        1) broker 取 mp4 字节；2) 上传唤星私有桶得 hasn://asset/；3) 落 studio_artifact；
        4) 回流通用产物索引 public.hasn_artifacts（同引 hasn://asset/）。任一步失败：不落 artifact、
        记录真实错误到 job.error（不覆盖既有 error 前缀），绝不 fake。
        """
        existing = (
            await db.execute(select(StudioArtifact.id).where(StudioArtifact.render_job_id == job.id).limit(1))
        ).scalar_one_or_none()
        if existing is not None:
            return

        # 取最新 snapshot 拿成片元数据（时长/分辨率/体积）；失败则用 job 已落的字段兜底。
        snapshot: dict[str, Any] = {}
        try:
            snapshot = await montage_engine_provider.get_render(job.engine_job_id)
        except StudioEngineError:
            snapshot = {}

        try:
            data, content_type = await montage_engine_provider.fetch_artifact(job.engine_job_id)
            ref = await StorageService.upload(
                db,
                data,
                category=_ARTIFACT_UPLOAD_CATEGORY,
                filename=f'{job.engine_job_id}.mp4',
                content_type=content_type or 'video/mp4',
            )
            asset = await hasn_asset_service.register_asset(
                db,
                owner_hasn_id=job.owner_hasn_id,
                ref=ref,
                kind='video',
                duration_ms=_duration_ms(snapshot),
                extract_status='done',  # 成片不进抽取流水线
            )
        except StudioEngineError as exc:
            # 取片传输失败：job 维持 succeeded（渲染本身已成功），只记录物化错误，绝不 fake artifact。
            job.error = f'成品物化失败（取片）：{exc}'
            await db.flush()
            return
        except Exception as exc:
            logger.warning('studio 成品物化失败（上传/落资产）: %s', exc)
            job.error = f'成品物化失败（上传）：{exc.__class__.__name__}: {exc}'
            await db.flush()
            return

        asset_uri = _asset_uri(asset.asset_id)
        artifact = StudioArtifact(
            project_id=job.project_id,
            render_job_id=job.id,
            owner_hasn_id=job.owner_hasn_id,
            agent_hasn_id=job.agent_hasn_id,
            title=_artifact_title(job, snapshot),
            pipeline_key=job.pipeline_key or None,
            video_asset_uri=asset_uri,
            duration_sec=_duration_decimal(snapshot),
            resolution=snapshot.get('resolution'),
            status='completed',
            origin_type='app',
            active_work_session_id=job.work_session_id,
            meta={
                'size_bytes': snapshot.get('size_bytes') or ref.size,
                'cost': job.cost,
                'engine_job_id': job.engine_job_id,
            },
        )
        db.add(artifact)
        await db.flush()

        # 回流通用产物索引（AF-*）：video 类型，引同一 hasn://asset/。
        if job.agent_hasn_id:
            try:
                await hasn_artifacts_service.record(
                    db,
                    agent_hasn_id=job.agent_hasn_id,
                    owner_hasn_id=job.owner_hasn_id,
                    params=RecordArtifactParam(
                        kind='other',  # 通用索引白名单无 'video' → 归 other（meta 标 media_kind=video）
                        title=artifact.title,
                        asset_id=asset.asset_id,
                        resource_uri=asset_uri,
                        session_id=job.work_session_id,
                        source_tool='hasn.studio.run_pipeline',
                        source_kind='tool_output',
                        dispatch_id=f'studio:render:{job.id}',  # 去重键，惰性轮询重入幂等
                        metadata={
                            'media_kind': 'video',
                            'pipeline_key': job.pipeline_key,
                            'resolution': artifact.resolution,
                            'studio_artifact_id': artifact.id,
                        },
                    ),
                )
            except Exception as exc:
                logger.warning('studio 成品回流 hasn_artifacts 失败: %s', exc)

    # ================================================================ 内部加载（owner 隔离）

    @staticmethod
    async def _load_project(db: AsyncSession, *, owner_hasn_id: str, project_id: int) -> StudioProject:
        stmt = select(StudioProject).where(StudioProject.id == project_id, StudioProject.owner_hasn_id == owner_hasn_id)
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='视频项目不存在')
        return row

    @staticmethod
    async def _load_render_job(db: AsyncSession, *, owner_hasn_id: str, render_job_id: int) -> StudioRenderJob:
        stmt = select(StudioRenderJob).where(
            StudioRenderJob.id == render_job_id, StudioRenderJob.owner_hasn_id == owner_hasn_id
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='渲染任务不存在')
        return row

    @staticmethod
    async def _load_artifact(db: AsyncSession, *, owner_hasn_id: str, artifact_id: int) -> StudioArtifact:
        stmt = select(StudioArtifact).where(
            StudioArtifact.id == artifact_id, StudioArtifact.owner_hasn_id == owner_hasn_id
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='成品不存在')
        return row

    @staticmethod
    async def _resolve_asset_urls(db: AsyncSession, *, owner_hasn_id: str, uris: list[str | None]) -> dict[str, str]:
        """批量把 hasn://asset/<id> 换 owner 可读签名 URL；返回 {原始 uri: signed_url}。"""
        uri_to_id: dict[str, str] = {}
        for uri in uris:
            aid = _asset_id_from_uri(uri)
            if aid and uri:
                uri_to_id[uri] = aid
        if not uri_to_id:
            return {}
        resolved = await hasn_asset_service.resolve(
            db, requester_hasn_id=owner_hasn_id, asset_ids=list(dict.fromkeys(uri_to_id.values())), conversation_id=None
        )
        id_to_url = {r.asset_id: r.display_url for r in resolved}
        return {uri: id_to_url[aid] for uri, aid in uri_to_id.items() if aid in id_to_url}


# ============================ 序列化 ============================


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _coerce_status(value: Any, *, default: str) -> str:
    allowed = ('queued', 'running', 'succeeded', 'failed', 'canceled')
    return value if value in allowed else default


def _build_cost(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """从引擎 snapshot 提成本计量落 ``studio_render_job.cost``（doc22 §5 P7 metering）。

    **只透传引擎真给的字段，绝不臆造**：``duration_sec``（合成段 CPU 计量）+ 引擎 ``cost`` 对象里的
    ``total_usd / credits / gpu_sec / provider_usage`` 等（OpenMontage ``cost_tracker`` 的 reconcile 结果）。

    **唤星账本折算（已接 vs 待办）**：网关族（image/tts/stt/video）经 new-api 用主人**自己**的 relay token
    出账，其用量**已自动**并入主人 new-api 账本（无需此处二次扣减——这是走网关路的根本好处）。BYO 长尾
    provider（fal/Suno/HeyGen）由主人自带 key 直接对 provider 付费，唤星不经手金钱；如未来要把 BYO 用量也
    折算成唤星积分扣减，是独立账本钩子——见下 ``# TODO(P9)``，本期只如实落 cost、不做二次扣减。
    """
    cost: dict[str, Any] = {}
    if snapshot.get('duration_sec') is not None:
        cost['duration_sec'] = snapshot['duration_sec']
    if snapshot.get('cost') is not None and isinstance(snapshot['cost'], dict):
        cost.update(snapshot['cost'])
    # TODO(P9): 若要把 BYO 长尾 provider 的 total_usd 折算成唤星积分扣减（gateway 族已自动并账，无需此步），
    #           在此处对 cost['total_usd'] 调 credit_service 扣减一次（带 idempotency key = engine_job_id 防重）。
    return cost or None


def _duration_ms(snapshot: dict[str, Any]) -> int | None:
    """成片时长（毫秒）——引擎 snapshot 的 duration_sec 是渲染耗时，非视频时长；只有引擎显式给视频时长才用。

    引擎当前 snapshot 无独立视频时长字段 → 留空（诚实），不拿渲染耗时冒充视频时长。
    """
    video_sec = snapshot.get('video_duration_sec')
    if video_sec is None:
        return None
    try:
        return int(float(video_sec) * 1000)
    except (TypeError, ValueError):
        return None


def _duration_decimal(snapshot: dict[str, Any]) -> Decimal | None:
    video_sec = snapshot.get('video_duration_sec')
    if video_sec is None:
        return None
    try:
        return Decimal(str(video_sec))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _artifact_title(job: StudioRenderJob, snapshot: dict[str, Any]) -> str:
    pk = job.pipeline_key or snapshot.get('pipeline_key') or 'render'
    return f'{pk} 成片'[:200]


def _serialize_project(
    row: StudioProject, *, my_permission: str | None = None, relation: str | None = None
) -> dict[str, Any]:
    out = {
        'id': row.id,
        'owner_hasn_id': row.owner_hasn_id,
        'agent_hasn_id': row.agent_hasn_id,
        'title': row.title,
        'description': row.description,
        'default_pipeline_key': row.default_pipeline_key,
        'settings': row.settings or {},
        'cover_asset_uri': row.cover_asset_uri,
        'bound_agent_id': row.bound_agent_id,
        'status': row.status,
        'created_time': _iso(getattr(row, 'created_time', None)),
        'updated_time': _iso(getattr(row, 'updated_time', None)),
    }
    if my_permission is not None:
        out['my_permission'] = my_permission
    if relation is not None:
        out['relation'] = relation
    return out


def _serialize_asset(row: StudioAsset) -> dict[str, Any]:
    return {
        'id': row.id,
        'project_id': row.project_id,
        'owner_hasn_id': row.owner_hasn_id,
        'kind': row.kind,
        'asset_uri': row.asset_uri,
        'source': row.source,
        'title': row.title,
        'meta': row.meta or {},
        'created_time': _iso(getattr(row, 'created_time', None)),
    }


def _serialize_render_job(row: StudioRenderJob) -> dict[str, Any]:
    return {
        'id': row.id,
        'project_id': row.project_id,
        'owner_hasn_id': row.owner_hasn_id,
        'agent_hasn_id': row.agent_hasn_id,
        'pipeline_key': row.pipeline_key,
        'input': row.input or {},
        'engine_job_id': row.engine_job_id,
        'status': row.status,
        'progress': row.progress,
        'stage': row.stage,
        'cost': row.cost,
        'work_session_id': row.work_session_id,
        'error': row.error,
        'started_at': _iso(row.started_at),
        'finished_at': _iso(row.finished_at),
        'created_time': _iso(getattr(row, 'created_time', None)),
    }


def _serialize_artifact(
    row: StudioArtifact,
    url_map: dict[str, str],
    *,
    my_permission: str | None = None,
    relation: str | None = None,
) -> dict[str, Any]:
    out = {
        'id': row.id,
        'project_id': row.project_id,
        'render_job_id': row.render_job_id,
        'owner_hasn_id': row.owner_hasn_id,
        'agent_hasn_id': row.agent_hasn_id,
        'title': row.title,
        'pipeline_key': row.pipeline_key,
        'video_asset_uri': row.video_asset_uri,
        'video_url': url_map.get(row.video_asset_uri),  # 序列化边界签名 URL（可空，零 fake）
        'thumbnail_asset_uri': row.thumbnail_asset_uri,
        'thumbnail_url': url_map.get(row.thumbnail_asset_uri) if row.thumbnail_asset_uri else None,
        'duration_sec': _to_float(row.duration_sec),
        'resolution': row.resolution,
        'status': row.status,
        'origin_type': row.origin_type,
        'active_work_session_id': row.active_work_session_id,
        'meta': row.meta or {},
        'created_time': _iso(getattr(row, 'created_time', None)),
    }
    if my_permission is not None:
        out['my_permission'] = my_permission
    if relation is not None:
        out['relation'] = relation
    return out


studio_service = StudioService()
