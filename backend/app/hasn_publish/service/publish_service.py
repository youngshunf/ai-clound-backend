"""通用网页发布与分享 service（owner 隔离 + slug + 可见性 + 口令 + 过期 + 访问票）。

设计事实源：docs/hasn-node设计文档/18-通用网页发布与分享/{01-数据模型与权限,03-工具与接口}.md。
- site/revision 双写：create=site+rev1+移动 current_revision_id；update=新 rev（content_hash 去重）+移动指针。
- 所有读写强制 owner_id 隔离（agent 代发布也落 owner 名下）。
- 可见性序 private<password<unlisted<public；password 进出清空/写 hash。
- 浏览器侧 private 访问票：短时签名 JWT，绑定 site_id（[01] §3.1）。
- bundle-zip 物化异步化（2026-08-29）：读 zip + 逐对象 PUT 对象存储实测 38s+，超过 daemon 写死的
  30s 上游超时（当日生产事故：客户端 499 放弃、服务端 200 落库、非幂等重试一晚重复发布 4 次）。
  bundle-zip 请求内只落 pending revision 立即返回，Celery worker 物化完成后才翻
  current_revision_id 指针；其余 runtime 无 fan-out，维持请求内同步物化。
"""

from __future__ import annotations

import io
import mimetypes
import secrets
import zipfile

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from jose import JWTError, jwt
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import func, select, text, update

from backend.app.hasn.service.hasn_asset_service import hasn_asset_service
from backend.app.hasn_project.model.hasn_project import HasnProject
from backend.app.hasn_publish.model import Revision, Site
from backend.common.exception import errors
from backend.common.log import log
from backend.core.conf import settings
from backend.plugin.s3.service.storage_service import storage_service
from backend.plugin.s3.utils.file_ops import write_bytes
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# ---- 常量 ----
_SLUG_ALPHABET = '23456789abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ'  # 去易混字符
_SLUG_LEN = 12
_SLUG_MAX_RETRY = 6
VISIBILITY_ORDER = ('private', 'password', 'unlisted', 'public')
# 'video'：studio 统一视频引擎成片对外发布（doc22 §3.6 / §9 S18，M18 web 发布全复用；列是开放字符串，无 DDL 变更）。
# 'design'：OpenPencil 矢量设计成品对外发布（doc27 §P3-C / OP-P3-9，A 成品分享复用 M18；同为开放字符串，无 DDL 变更）。
VALID_KINDS = ('deck', 'report', 'page', 'dashboard', 'video', 'design', 'other')
MAX_REVISIONS_PER_SITE = 20
# 标题长度上限：与 app/agent 两侧请求模型的 max_length 同源，改这里就要一起改（列宽 200）。
MAX_TITLE_LEN = 200
VIEW_TICKET_TTL_SECONDS = 600  # 10 分钟
_VIEW_TICKET_TYPE = 'publish_view_ticket'
FORM_ACCESS_TOKEN_TTL_SECONDS = 600
GROWTH_LEAD_FORM_REF = 'growth-lead-v1'
_FORM_ACCESS_TOKEN_TYPE = 'publish_form_access'

# ---- bundle-zip 物化异步化（2026-08-29，见模块 docstring） ----

#: 需要异步物化的 runtime 闭集：只有 bundle-zip 有「读 zip + 逐对象 PUT」的公网 fan-out。
#: referenced 资产是同桶 server-side copy（亚秒级），不因此进异步路径。
ASYNC_MATERIALIZE_RUNTIMES = frozenset({'bundle-zip'})

#: revision.materialize_status 三态（仅 bundle-zip 会出现非 ready）
MATERIALIZE_PENDING = 'pending'
MATERIALIZE_READY = 'ready'
MATERIALIZE_FAILED = 'failed'

#: sweep 宽限期：正常 after_commit 派发到 worker 捡走是秒级；created_time 超过此时长仍
#: pending 才判定为滞留（派发失败 / worker 中断 / broker 抖动），由每分钟 sweep 重新入队。
_SWEEP_GRACE = timedelta(minutes=2)

_password_hasher = PasswordHash((BcryptHasher(),))


def visibility_rank(visibility: str) -> int:
    """可见性级别（越高越公开）；未知视作 private（0）。"""
    try:
        return VISIBILITY_ORDER.index(visibility)
    except ValueError:
        return 0


def _gen_slug() -> str:
    return ''.join(secrets.choice(_SLUG_ALPHABET) for _ in range(_SLUG_LEN))


def hash_password(plain: str) -> str:
    return _password_hasher.hash(plain)


# ---- 发布时物化：bundle-zip 解包 + 资产引用 server-side copy ----

_BUNDLE_ENTRY_NAME = 'index.html'

# daemon 打包核心 manifest assets[] 项的状态值（hasn-web-package AssetStatus）。
_ASSET_STATUS_REFERENCED = 'referenced'


def _is_safe_bundle_member(name: str) -> bool:
    """bundle 成员名必须是干净的相对路径（拒绝绝对路径、反斜杠、空段与 `.`/`..` 段）。

    zip 来自 daemon 打包核心（hasn-web-package），可信但仍做纵深校验：
    子对象 key 直接由成员名拼接，恶意/损坏成员名不能越出 `owners/{owner}/publish/{asset_id}/` 前缀。
    """
    if not name or name.startswith('/') or '\\' in name:
        return False
    return all(part not in ('', '.', '..') for part in name.split('/'))


async def _unpack_bundle_zip_files(db: AsyncSession, *, owner_id: str, asset_id: str) -> dict[str, Any]:
    """bundle-zip 的「发布时解包」：读 zip 制品 → 逐对象写回同 storage → files 条目表。

    契约背景：daemon 打包核心把 bundle-zip（`index.html` + `assets/*`）整体上传为一个
    zip asset，并附带打包侧 manifest（`{entry, assets[]}`，无对象存储坐标）；而 serve 侧
    （`api/v1/open/hosting.py` 的 `_bundle_entry`）按 `{files: {name: {object_key, ...}}}`
    逐对象代吐。两者之间必须有人在发布时把 zip 真正解开写对象——本函数就是那个环节。
    缺了它，manifest 里永远没有 object_key，`/s/{slug}` 只会 410「bundle 缺少入口 index.html」。

    零 fake：zip 损坏、成员路径非法、缺入口一律显式报错（daemon 契约错误），不产出残缺 manifest。
    """
    asset = await hasn_asset_service.get_by_asset_id(db, asset_id)
    if asset is None:
        raise errors.RequestError(msg='制品 asset 不存在')
    zip_bytes = await storage_service.read_bytes(db, storage_id=asset.storage_id, object_key=asset.object_key)
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise errors.ServerError(msg='bundle-zip 制品不是合法 zip') from exc
    storage = await storage_service.get_storage(db, asset.storage_id)
    # 子对象 key 挂 owner/publish/{asset_id} 前缀：owner 隔离 + 每次发布新 asset 天然不撞键。
    prefix = f'owners/{owner_id}/publish/{asset_id}'
    files: dict[str, Any] = {}
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not _is_safe_bundle_member(name):
                raise errors.ServerError(msg=f'bundle-zip 成员路径非法: {name}')
            data = archive.read(info)
            mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
            if name == _BUNDLE_ENTRY_NAME:
                mime = 'text/html'
            object_key = f'{prefix}/{name}'
            await write_bytes(storage, object_key, data, mime)
            files[name] = {
                'object_key': object_key,
                'mime': mime,
                'size': len(data),
                'storage_id': asset.storage_id,
            }
    if _BUNDLE_ENTRY_NAME not in files:
        raise errors.ServerError(msg='bundle-zip 缺少入口 index.html')
    return files


async def _materialize_referenced_assets(
    db: AsyncSession, *, owner_id: str, publish_asset_id: str, manifest: dict[str, Any] | None
) -> dict[str, Any]:
    """把 manifest.assets 里 `status='referenced'` 的 hasn 资产物化为发布前缀下的真实对象。

    资产引用化契约：deck 页里的图本来就在对象存储桶里（`hasn://asset/{id}`），daemon 打包核心
    只登记引用、不再把字节下载回本机又上传一遍；云端发布时按 asset_id 做**同桶 server-side
    copy**（S3 CopyObject，零公网流量）到发布前缀——发布后主人删改原图不影响已发布快照。

    零 fake：referenced 项缺 asset_id/name、资产不存在、**资产不属于发布者**（防 ACL 绕过）
    一律显式报错，不静默跳过。
    """
    assets = manifest.get('assets') if isinstance(manifest, dict) else None
    if not isinstance(assets, list):
        return {}
    files: dict[str, Any] = {}
    for item in assets:
        if not isinstance(item, dict) or item.get('status') != _ASSET_STATUS_REFERENCED:
            continue
        ref_asset_id = item.get('asset_id')
        name = item.get('name')
        if (
            not isinstance(ref_asset_id, str)
            or not ref_asset_id
            or not isinstance(name, str)
            or not _is_safe_bundle_member(name)
        ):
            raise errors.RequestError(msg=f'referenced 资产项缺 asset_id/name 或 name 非法: {item!r}')
        record = await hasn_asset_service.get_by_asset_id(db, ref_asset_id)
        if record is None or record.owner_hasn_id != owner_id:
            # 主人可修正的输入问题（图片已被清理/从未同步），按 4xx 返回并给出可操作的指引，
            # 不抛 500——前端 toast 会把这段文案直接呈给主人。
            raise errors.RequestError(
                msg=f'发布内容里有图片已失效或未同步到云端（{ref_asset_id}），请在编辑器中替换或删除该图片后重试'
            )
        storage = await storage_service.get_storage(db, record.storage_id)
        target_key = f'owners/{owner_id}/publish/{publish_asset_id}/{name}'
        await storage_service.copy_between_storages(
            storage,
            source_key=record.object_key,
            target=storage,
            target_key=target_key,
            size=record.size_bytes,
            content_type=record.mime,
        )
        files[name] = {
            'object_key': target_key,
            'mime': record.mime,
            'size': record.size_bytes,
            'storage_id': record.storage_id,
        }
    return files


async def _materialize_publish_manifest(
    db: AsyncSession, *, owner_id: str, asset_id: str, runtime: str, manifest_json: dict[str, Any] | None
) -> dict[str, Any] | None:
    """发布时物化统一入口：bundle-zip 解包 + referenced 资产 copy，files 合并进 manifest。

    serve 侧只认 manifest.files 的 object_key 坐标；打包侧 manifest 的其余键
    （runtime/format/entry/assets[]/failures）原样保留作溯源。
    """
    files: dict[str, Any] = {}
    if runtime == 'bundle-zip':
        files.update(await _unpack_bundle_zip_files(db, owner_id=owner_id, asset_id=asset_id))
    files.update(
        await _materialize_referenced_assets(db, owner_id=owner_id, publish_asset_id=asset_id, manifest=manifest_json)
    )
    if not files:
        return manifest_json  # single-html 且无资产引用：manifest 原样（可为 None）
    manifest = dict(manifest_json or {})
    manifest['files'] = files
    return manifest


def _dispatch_materialize_after_commit(db: AsyncSession, revision_id: int) -> None:
    """注册 after_commit 钩子：本事务真正提交后才把物化任务入 Celery 队列。

    与 growth 采集同一范式（growth_tool_handlers._enqueue_collection_job_after_commit）：
    提前 .delay() 会让 worker 读到不存在（未提交）的 revision；事务回滚则钩子不触发
    （不会出现"入了队却无 revision"的孤儿任务）。broker 不可达时 best-effort warn——
    pending 行由每分钟 sweep（publish_materialize_sweep）兜底重派，不会丢。
    """
    from sqlalchemy import event

    def _enqueue(_sync_session: Any) -> None:
        try:
            # 延迟 import：tasks 模块反向 import 本 service，模块级互引成环
            from backend.app.hasn_publish.tasks import publish_materialize_revision

            publish_materialize_revision.delay(revision_id)
            log.info(f'[Publish] 物化任务已入队: revision_id={revision_id}')
        except Exception as exc:
            log.warning(
                '[Publish] 物化任务入队失败，等 sweep 兜底: revision_id=%s error_type=%s',
                revision_id,
                exc.__class__.__name__,
            )

    event.listen(db.sync_session, 'after_commit', _enqueue, once=True)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _password_hasher.verify(plain, hashed)
    except Exception:
        return False


def site_to_dict(site: Site) -> dict[str, Any]:
    """序列化 site（不含 password_hash；password 明文仅在本 owner/agent 通道回读）。

    `password` 回读是 2026-08-19 产品裁决：分享口令「防访客不防主人」（类比网盘提取码），
    主人在任一设备可查看口令、复制带口令链接。open/meta/hosting 访客面不经过本函数，
    明文绝不外露。
    """
    return {
        'id': site.id,
        'owner_id': site.owner_id,
        'publisher_agent_id': site.publisher_agent_id,
        'kind': site.kind,
        'title': site.title,
        'slug': site.slug,
        'source_app': site.source_app,
        'source_ref': site.source_ref,
        'platform_project_id': str(site.platform_project_id) if site.platform_project_id else None,
        'current_revision_id': site.current_revision_id,
        'status': site.status,
        'visibility': site.visibility,
        'has_password': bool(site.password_hash),
        'password': site.password_plain if site.visibility == 'password' else None,
        'expires_at': timezone.to_str(site.expires_at) if site.expires_at else None,
        'allow_present': site.allow_present,
        'allow_download': site.allow_download,
        'allow_indexing': site.allow_indexing,
        'view_count': site.view_count,
        'rev': site.rev,
        'created_time': timezone.to_str(site.created_time) if site.created_time else None,
        'updated_time': timezone.to_str(site.updated_time) if site.updated_time else None,
    }


def revision_to_dict(rev: Revision) -> dict[str, Any]:
    return {
        'id': rev.id,
        'site_id': rev.site_id,
        'seq': rev.seq,
        'asset_id': rev.asset_id,
        'runtime': rev.runtime,
        'content_hash': rev.content_hash,
        'size_bytes': rev.size_bytes,
        'manifest_json': rev.manifest_json,
        'materialize_status': rev.materialize_status,
        'materialize_error': rev.materialize_error,
        'created_time': timezone.to_str(rev.created_time) if rev.created_time else None,
    }


class PublishService:
    """owner 隔离的发布服务。所有方法第一参数 db，关键字 owner_id 为隔离键。"""

    # ---------------- 校验 ----------------

    @staticmethod
    def _validate_visibility(visibility: str, password: str | None) -> None:
        if visibility not in VISIBILITY_ORDER:
            raise errors.RequestError(msg=f'非法可见性：{visibility}')
        if visibility == 'password' and not password:
            raise errors.RequestError(msg='可见性为 password 时必须提供口令')

    @staticmethod
    def _validate_kind(kind: str) -> str:
        return kind if kind in VALID_KINDS else 'other'

    @staticmethod
    def normalize_title(title: str | None) -> str:
        """归一展示标题：去首尾空白 → 必须非空 → 限长。

        零 fake：不给「未命名」这类兜底值——发布方没给名字就是没给，当场报错让它补，
        而不是落一条主人在列表里认不出来的记录。
        """
        normalized = (title or '').strip()
        if not normalized:
            raise errors.RequestError(msg='发布标题不能为空')
        if len(normalized) > MAX_TITLE_LEN:
            raise errors.RequestError(msg=f'发布标题不能超过 {MAX_TITLE_LEN} 个字符')
        return normalized

    @staticmethod
    async def _resolve_owned_project_id(
        db: AsyncSession,
        *,
        owner_id: str,
        platform_project_id: str | UUID | None,
    ) -> UUID | None:
        """校验可选项目挂靠；客户端传值只用于定位，Owner 与状态均由云端权威表判定。"""
        if platform_project_id is None or not str(platform_project_id).strip():
            return None
        try:
            project_id = UUID(str(platform_project_id))
        except ValueError as exc:
            raise errors.RequestError(msg='平台项目 ID 必须是有效 UUID') from exc
        project = (
            await db.execute(
                select(HasnProject).where(
                    HasnProject.id == project_id,
                    HasnProject.owner_id == owner_id,
                )
            )
        ).scalar_one_or_none()
        if project is None:
            raise errors.NotFoundError(msg='平台项目不存在或不属于你')
        if project.status != 'active':
            raise errors.ConflictError(
                msg='项目已归档，不能创建新的发布站点',
                data={'error_code': 'PROJECT_ARCHIVED'},
            )
        return project.id

    # ---------------- slug ----------------

    @staticmethod
    async def _alloc_slug(db: AsyncSession) -> str:
        for _ in range(_SLUG_MAX_RETRY):
            slug = _gen_slug()
            exists = (await db.execute(select(Site.id).where(Site.slug == slug).limit(1))).scalar_one_or_none()
            if exists is None:
                return slug
        raise errors.ServerError(msg='slug 分配失败（多次冲突）')

    # ---------------- create ----------------

    @staticmethod
    async def create_site(
        db: AsyncSession,
        *,
        owner_id: str,
        publisher_agent_id: str | None = None,
        kind: str = 'page',
        title: str = '',
        asset_id: str,
        runtime: str = 'single-html',
        content_hash: str = '',
        size_bytes: int = 0,
        manifest_json: dict | None = None,
        visibility: str = 'private',
        password: str | None = None,
        expires_at: datetime | None = None,
        allow_present: bool = True,
        allow_download: bool = False,
        allow_indexing: bool = False,
        source_app: str | None = None,
        source_ref: str | None = None,
        platform_project_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        PublishService._validate_visibility(visibility, password)
        if not asset_id:
            raise errors.RequestError(msg='缺少制品 asset_id')
        resolved_project_id = await PublishService._resolve_owned_project_id(
            db,
            owner_id=owner_id,
            platform_project_id=platform_project_id,
        )
        if source_app == 'growth':
            normalized_source_ref = (source_ref or '').strip()
            if not normalized_source_ref or resolved_project_id is None:
                raise errors.RequestError(msg='Growth 落地页必须绑定来源获客项目与平台项目')
            await db.execute(
                text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
                {'lock_key': f'publish:growth:{owner_id}:{normalized_source_ref}'},
            )
            existing = (
                await db.execute(
                    select(Site).where(
                        Site.owner_id == owner_id,
                        Site.source_app == 'growth',
                        Site.source_ref == normalized_source_ref,
                        Site.deleted_time.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.platform_project_id != resolved_project_id:
                    raise errors.ConflictError(
                        msg='该 Growth 来源站点已属于其他平台项目',
                        data={'error_code': 'PUBLISH_GROWTH_PROJECT_MISMATCH'},
                    )
                current_revision = await PublishService.get_current_revision(db, site_id=existing.id)
                same_content = current_revision is not None and (
                    (bool(content_hash) and current_revision.content_hash == content_hash)
                    or (not content_hash and current_revision.asset_id == asset_id)
                )
                if same_content and current_revision is not None:
                    return {
                        'site': site_to_dict(existing),
                        'revision': revision_to_dict(current_revision),
                        'reused': True,
                    }
                existing.title = title or existing.title
                existing.status = 'active'
                existing.visibility = visibility
                existing.password_hash = (
                    hash_password(password) if visibility == 'password' and password else None
                )
                existing.password_plain = password if visibility == 'password' and password else None
                existing.expires_at = expires_at
                existing.allow_present = allow_present
                existing.allow_download = allow_download
                existing.allow_indexing = allow_indexing if visibility == 'public' else False
                updated = await PublishService.update_site(
                    db,
                    owner_id=owner_id,
                    site_id=existing.id,
                    asset_id=asset_id,
                    runtime=runtime,
                    content_hash=content_hash,
                    size_bytes=size_bytes,
                    manifest_json=manifest_json,
                )
                updated['site'] = site_to_dict(existing)
                return updated
            source_ref = normalized_source_ref
        slug = await PublishService._alloc_slug(db)
        site = Site(
            owner_id=owner_id,
            publisher_agent_id=publisher_agent_id,
            kind=PublishService._validate_kind(kind),
            # 没给标题就**存空**，不再兜底成 '未命名'——那是个假名字：它在主人的「网页发布」
            # 里长得和真标题一模一样（黑体、不可疑），主人既认不出这是哪个站，也看不出
            # 「这里缺一个名字」。空标题由展示层各自降级（访客外壳已有 `title or '分享'`，
            # WebUI 显示成可点的「起个名字」入口），数据层保持诚实。
            title=(title or '').strip(),
            slug=slug,
            source_app=source_app,
            source_ref=source_ref,
            platform_project_id=resolved_project_id,
            current_revision_id=None,
            status='active',
            visibility=visibility,
            password_hash=hash_password(password) if (visibility == 'password' and password) else None,
            password_plain=password if (visibility == 'password' and password) else None,
            expires_at=expires_at,
            allow_present=allow_present,
            allow_download=allow_download,
            allow_indexing=allow_indexing if visibility == 'public' else False,
            view_count=0,
            rev=1,
        )
        db.add(site)
        await db.flush()  # 取 site.id

        # 发布时物化：bundle-zip 走异步（落 pending revision 立即返回，Celery worker 物化完成后
        # 才翻 current_revision_id——翻转前 /s/{slug} 由 serve 侧呈现「发布进行中」过渡页）；
        # 其余 runtime 无公网 fan-out，维持请求内同步物化 + 立即翻指针。
        if runtime in ASYNC_MATERIALIZE_RUNTIMES:
            materialize_status = MATERIALIZE_PENDING
        else:
            manifest_json = await _materialize_publish_manifest(
                db, owner_id=owner_id, asset_id=asset_id, runtime=runtime, manifest_json=manifest_json
            )
            materialize_status = MATERIALIZE_READY

        revision = Revision(
            site_id=site.id,
            owner_id=owner_id,
            seq=1,
            asset_id=asset_id,
            runtime=runtime,
            content_hash=content_hash,
            size_bytes=size_bytes,
            manifest_json=manifest_json,
            materialize_status=materialize_status,
        )
        db.add(revision)
        await db.flush()  # 取 revision.id

        if materialize_status == MATERIALIZE_READY:
            site.current_revision_id = revision.id
            await db.flush()
        else:
            _dispatch_materialize_after_commit(db, revision.id)
        return {'site': site_to_dict(site), 'revision': revision_to_dict(revision)}

    # ---------------- update（新 revision） ----------------

    @staticmethod
    async def update_site(
        db: AsyncSession,
        *,
        owner_id: str,
        site_id: int,
        asset_id: str,
        runtime: str = 'single-html',
        content_hash: str = '',
        size_bytes: int = 0,
        manifest_json: dict | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        if not asset_id:
            raise errors.RequestError(msg='缺少制品 asset_id')
        # title 给了才改（不给＝只换内容，保留原名）；给了就必须是合法名字，不接受空串抹掉标题。
        if title is not None:
            site.title = PublishService.normalize_title(title)

        # content_hash 去重：site 内同 hash 复用 revision（不新起版本）
        if content_hash:
            dup = (
                await db.execute(
                    select(Revision)
                    .where(
                        Revision.site_id == site.id,
                        Revision.content_hash == content_hash,
                        Revision.deleted_time.is_(None),
                    )
                    .order_by(Revision.seq.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if dup is not None:
                # 按物化状态三分：
                # - ready   → 仅移动指针（原语义）
                # - pending → 物化任务已在跑/排队，完成后自行翻指针，这里不动指针
                # - failed  → 同内容重试 = 重置回 pending 重新入队（诚实重试，不另起 revision）
                if dup.materialize_status == MATERIALIZE_FAILED:
                    dup.materialize_status = MATERIALIZE_PENDING
                    dup.materialize_error = None
                    _dispatch_materialize_after_commit(db, dup.id)
                elif dup.materialize_status == MATERIALIZE_READY:
                    site.current_revision_id = dup.id
                site.status = 'active'
                site.rev += 1
                await db.flush()
                return {'site': site_to_dict(site), 'revision': revision_to_dict(dup), 'reused': True}

        next_seq = (
            await db.execute(select(func.coalesce(func.max(Revision.seq), 0)).where(Revision.site_id == site.id))
        ).scalar_one() + 1
        # 发布时物化：与 create 同一环节、同一异步边界。pending 期间指针留在旧 revision——
        # 物化完成前旧内容继续可访问，任务完成才原子翻转（不会出现「新内容没好、旧内容先没」）。
        if runtime in ASYNC_MATERIALIZE_RUNTIMES:
            materialize_status = MATERIALIZE_PENDING
        else:
            manifest_json = await _materialize_publish_manifest(
                db, owner_id=owner_id, asset_id=asset_id, runtime=runtime, manifest_json=manifest_json
            )
            materialize_status = MATERIALIZE_READY
        revision = Revision(
            site_id=site.id,
            owner_id=owner_id,
            seq=next_seq,
            asset_id=asset_id,
            runtime=runtime,
            content_hash=content_hash,
            size_bytes=size_bytes,
            manifest_json=manifest_json,
            materialize_status=materialize_status,
        )
        db.add(revision)
        await db.flush()
        if materialize_status == MATERIALIZE_READY:
            site.current_revision_id = revision.id
        site.status = 'active'  # update 自动复活已撤销 site
        site.rev += 1
        await db.flush()
        await PublishService._gc_revisions(db, site_id=site.id, keep_id=revision.id)
        if materialize_status == MATERIALIZE_PENDING:
            _dispatch_materialize_after_commit(db, revision.id)
        return {'site': site_to_dict(site), 'revision': revision_to_dict(revision), 'reused': False}

    @staticmethod
    async def _gc_revisions(db: AsyncSession, *, site_id: int, keep_id: int) -> None:
        """保留每 site 最近 MAX_REVISIONS_PER_SITE 个 revision（软删超出的旧版本，当前指针除外）。"""
        rows = (
            (
                await db.execute(
                    select(Revision.id)
                    .where(Revision.site_id == site_id, Revision.deleted_time.is_(None))
                    .order_by(Revision.seq.desc())
                )
            )
            .scalars()
            .all()
        )
        for stale_id in rows[MAX_REVISIONS_PER_SITE:]:
            if stale_id == keep_id:
                continue
            rev = await db.get(Revision, stale_id)
            if rev is not None:
                rev.deleted_time = timezone.now()
        await db.flush()

    # ---------------- visibility / revoke / delete ----------------

    @staticmethod
    async def set_visibility(
        db: AsyncSession,
        *,
        owner_id: str,
        site_id: int,
        visibility: str | None = None,
        password: str | None = None,
        clear_password: bool = False,
        expires_at: datetime | None = None,
        clear_expires: bool = False,
        allow_present: bool | None = None,
        allow_download: bool | None = None,
        allow_indexing: bool | None = None,
    ) -> dict[str, Any]:
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        target_vis = visibility if visibility is not None else site.visibility
        # 进入 password 必须有口令（或已有 hash）
        if target_vis == 'password' and not (password or site.password_hash):
            raise errors.RequestError(msg='切换到 password 可见性必须提供口令')
        if visibility is not None:
            if visibility not in VISIBILITY_ORDER:
                raise errors.RequestError(msg=f'非法可见性：{visibility}')
            site.visibility = visibility
            # 不变量 3：离开 password 清空 hash 与明文回读列
            if visibility != 'password':
                site.password_hash = None
                site.password_plain = None
        if password:
            site.password_hash = hash_password(password)
            site.password_plain = password
        elif clear_password and site.visibility != 'password':
            site.password_hash = None
            site.password_plain = None
        if clear_expires:
            site.expires_at = None
        elif expires_at is not None:
            site.expires_at = expires_at
        if allow_present is not None:
            site.allow_present = allow_present
        if allow_download is not None:
            site.allow_download = allow_download
        if allow_indexing is not None:
            site.allow_indexing = allow_indexing if site.visibility == 'public' else False
        if site.visibility != 'public':
            site.allow_indexing = False
        site.rev += 1
        await db.flush()
        return site_to_dict(site)

    @staticmethod
    async def rename_site(
        db: AsyncSession, *, owner_id: str, site_id: int, title: str
    ) -> dict[str, Any]:
        """改展示标题（纯元数据，不发新 revision、不动 slug/URL/可见性）。

        与 update_site 分开：改名不该产生一个新版本，也不该要求调用方重新上传制品。
        """
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        site.title = PublishService.normalize_title(title)
        site.rev += 1
        await db.flush()
        return site_to_dict(site)

    @staticmethod
    async def revoke(db: AsyncSession, *, owner_id: str, site_id: int) -> dict[str, Any]:
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        site.status = 'revoked'
        site.current_revision_id = None
        site.rev += 1
        await db.flush()
        return site_to_dict(site)

    @staticmethod
    async def delete_site(db: AsyncSession, *, owner_id: str, site_id: int) -> None:
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        site.deleted_time = timezone.now()
        site.status = 'revoked'
        site.rev += 1
        await db.flush()

    # ---------------- 读 ----------------

    @staticmethod
    async def _get_owned(db: AsyncSession, *, owner_id: str, site_id: int) -> Site:
        site = (
            await db.execute(
                select(Site).where(Site.id == site_id, Site.owner_id == owner_id, Site.deleted_time.is_(None))
            )
        ).scalar_one_or_none()
        if site is None:
            raise errors.NotFoundError(msg='发布不存在')
        return site

    @staticmethod
    async def get_owned(db: AsyncSession, *, owner_id: str, site_id: int) -> dict[str, Any]:
        site = await PublishService._get_owned(db, owner_id=owner_id, site_id=site_id)
        latest = await PublishService.get_latest_revision(db, site_id=site_id)
        return {
            'site': site_to_dict(site),
            # 轮询面：bundle-zip 异步物化后，调用方按 latest_revision.materialize_status 判
            # pending/ready/failed——current_revision 指针只在 ready 时翻转，代表不了在途状态
            'latest_revision': revision_to_dict(latest) if latest is not None else None,
        }

    @staticmethod
    async def list_owned(
        db: AsyncSession, *, owner_id: str, kind: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        base = select(Site).where(Site.owner_id == owner_id, Site.deleted_time.is_(None))
        if kind:
            base = base.where(Site.kind == kind)
        total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = (
            (
                await db.execute(
                    base.order_by(Site.updated_time.desc().nullslast(), Site.id.desc()).limit(limit).offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return {'items': [site_to_dict(s) for s in rows], 'total': int(total)}

    @staticmethod
    async def get_by_source(
        db: AsyncSession, *, owner_id: str, source_app: str, source_ref: str
    ) -> dict[str, Any] | None:
        site = (
            await db.execute(
                select(Site).where(
                    Site.owner_id == owner_id,
                    Site.source_app == source_app,
                    Site.source_ref == source_ref,
                    Site.deleted_time.is_(None),
                )
            )
        ).scalar_one_or_none()
        return site_to_dict(site) if site else None

    @staticmethod
    async def get_current_revision(db: AsyncSession, *, site_id: int) -> Revision | None:
        site = await db.get(Site, site_id)
        if site is None or site.current_revision_id is None:
            return None
        return await db.get(Revision, site.current_revision_id)

    @staticmethod
    async def get_latest_revision(db: AsyncSession, *, site_id: int) -> Revision | None:
        """site 的最新未删 revision（serve 侧判「发布进行中」与 GET 轮询面用）。"""
        return (
            await db.execute(
                select(Revision)
                .where(Revision.site_id == site_id, Revision.deleted_time.is_(None))
                .order_by(Revision.seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    # ---------------- 异步物化（bundle-zip，Celery worker 执行） ----------------

    @staticmethod
    async def materialize_revision(db: AsyncSession, *, revision_id: int) -> str:
        """物化一个 pending revision：对象存储 fan-out → 回写 manifest.files → ready → 翻 site 指针。

        幂等：`FOR UPDATE SKIP LOCKED`——重复消息/并发 worker 下同一 revision 只有一个在执行，
        其余当场跳过；已 ready/failed 的直接跳过。

        零 fake：确定性业务失败（制品缺失、zip 损坏/缺入口、referenced 资产失效）落
        `failed` + 主人可读文案并通知主人，绝不静默，也绝不抛给任务层重试（重试无意义）；
        其余意外异常（对象存储/网络/DB 抖动）原样抛出，由任务层退避重试。
        """
        revision = (
            await db.execute(
                select(Revision)
                .where(Revision.id == revision_id, Revision.deleted_time.is_(None))
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if revision is None:
            return 'skip:locked-or-deleted'
        if revision.materialize_status != MATERIALIZE_PENDING:
            return f'skip:{revision.materialize_status}'

        site = await db.get(Site, revision.site_id)
        if site is None or site.deleted_time is not None:
            # 站点在物化期间被删除：落终态避免每分钟 sweep 反复捞起，不通知（主人自己删的）
            revision.materialize_status = MATERIALIZE_FAILED
            revision.materialize_error = '发布已取消（站点已删除）'
            await db.flush()
            return 'failed:site-deleted'

        try:
            manifest_json = await _materialize_publish_manifest(
                db,
                owner_id=revision.owner_id,
                asset_id=revision.asset_id,
                runtime=revision.runtime,
                manifest_json=revision.manifest_json,
            )
        except (errors.RequestError, errors.ServerError) as exc:
            revision.materialize_status = MATERIALIZE_FAILED
            revision.materialize_error = exc.msg
            await db.flush()
            log.warning(f'[Publish] 物化业务失败: revision_id={revision_id}, site_id={site.id}, msg={exc.msg}')
            await PublishService._notify_materialize_failed(db, site=site, revision=revision)
            return f'failed:{exc.msg}'

        revision.manifest_json = manifest_json
        revision.materialize_status = MATERIALIZE_READY
        revision.materialize_error = None

        # 翻指针：行锁串行化并发物化；只许向更新的 seq 翻——同一 site 两个 pending 同时物化时，
        # 先完成的旧 seq 不许把指针从新 seq 上拽回来。
        # 已撤销/已删除的 site 不翻：serve 面按 status 判 410，翻了也是死指针，保持原状更诚实。
        locked_site = (
            await db.execute(select(Site).where(Site.id == site.id).with_for_update())
        ).scalar_one()
        if locked_site.deleted_time is None and locked_site.status != 'revoked':
            current = None
            if locked_site.current_revision_id is not None:
                current = await db.get(Revision, locked_site.current_revision_id)
            if current is None or current.seq < revision.seq:
                locked_site.current_revision_id = revision.id
        await db.flush()
        log.info(f'[Publish] 物化完成: revision_id={revision_id}, site_id={site.id}')
        return 'ready'

    @staticmethod
    async def mark_materialize_failed(db: AsyncSession, *, revision_id: int, error: str) -> str:
        """任务重试耗尽后的兜底落 failed（由任务层用独立会话调用——主事务已回滚）。

        幂等：非 pending 不动（可能已被 sweep 重派的新一轮执行救活）。
        """
        revision = (
            await db.execute(
                select(Revision)
                .where(Revision.id == revision_id, Revision.deleted_time.is_(None))
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if revision is None or revision.materialize_status != MATERIALIZE_PENDING:
            return 'skip'
        revision.materialize_status = MATERIALIZE_FAILED
        revision.materialize_error = error
        await db.flush()
        site = await db.get(Site, revision.site_id)
        if site is not None and site.deleted_time is None:
            await PublishService._notify_materialize_failed(db, site=site, revision=revision)
        return 'failed'

    @staticmethod
    async def find_stuck_pending_materializations(db: AsyncSession, *, limit: int = 50) -> list[int]:
        """滞留 pending 的 revision id 列表（created_time 早于宽限期），供每分钟 sweep 重新入队。

        不加行锁：物化任务本身幂等（SKIP LOCKED + 状态判据），重复入队无害；
        已删 site 的 pending 不捞（物化出来也没有读者）。
        """
        cutoff = timezone.now() - _SWEEP_GRACE
        rows = (
            await db.execute(
                select(Revision.id)
                .join(Site, Site.id == Revision.site_id)
                .where(
                    Revision.materialize_status == MATERIALIZE_PENDING,
                    Revision.deleted_time.is_(None),
                    Revision.created_time < cutoff,
                    Site.deleted_time.is_(None),
                )
                .order_by(Revision.id)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows)

    @staticmethod
    async def _notify_materialize_failed(db: AsyncSession, *, site: Site, revision: Revision) -> None:
        """物化失败告知主人（best-effort，收在 savepoint 里）。

        通知是「告知」，不是物化动作本身——通知侧故障绝不能把已落库的 failed 状态回滚掉
        （同 hosting 生命周期通知的纪律）。失败如实 warn，外层事务照常继续。
        """
        from backend.app.notification.service.notification_service import NotificationService

        try:
            async with db.begin_nested():
                await NotificationService.emit(
                    db,
                    recipient_id=site.owner_id,
                    source={'kind': 'system', 'id': 'publish'},
                    category='app',
                    type='publish_materialize_failed',
                    title=f'网页发布失败：{site.title or site.slug}',
                    body=revision.materialize_error or '发布处理失败，请重新发布',
                    payload={
                        'site_id': site.id,
                        'revision_id': revision.id,
                        'materialize_status': revision.materialize_status,
                    },
                    priority='high',
                    dedupe_key=f'publish_materialize_failed:{revision.id}',
                )
        except Exception as exc:
            log.warning(
                f'[Publish] 物化失败通知发送失败（不影响 failed 落库）: revision_id={revision.id}, err={exc}'
            )

    # ---------------- 公开查看（/s/{slug}，[03] §3） ----------------

    @staticmethod
    async def get_site_by_slug(db: AsyncSession, *, slug: str) -> Site | None:
        """按 slug 取 site（任意 owner，公开查看用；软删返回 None）。"""
        return (
            await db.execute(select(Site).where(Site.slug == slug, Site.deleted_time.is_(None)))
        ).scalar_one_or_none()

    @staticmethod
    def is_expired(site: Site) -> bool:
        return site.expires_at is not None and site.expires_at <= timezone.now()

    @staticmethod
    async def increment_view_count(db: AsyncSession, *, site_id: int) -> None:
        """访问计数 +1（best-effort，统计非鉴权；失败不抛）。"""
        try:
            await db.execute(update(Site).where(Site.id == site_id).values(view_count=Site.view_count + 1))
            await db.flush()
        except Exception:
            pass

    @staticmethod
    async def verify_unlock(db: AsyncSession, *, site: Site, password: str) -> bool:
        """password 可见性：校验口令（[03] §3 /unlock）。"""
        if site.visibility != 'password' or not site.password_hash:
            return False
        return verify_password(password, site.password_hash)

    # ---------------- 浏览器访问票（private，[01] §3.1） ----------------

    @staticmethod
    def issue_view_ticket(*, site_id: int, owner_id: str) -> dict[str, Any]:
        exp = timezone.now() + timedelta(seconds=VIEW_TICKET_TTL_SECONDS)
        payload = {
            'typ': _VIEW_TICKET_TYPE,
            'site_id': site_id,
            'owner_id': owner_id,
            'exp': timezone.to_utc(exp).timestamp(),
        }
        ticket = jwt.encode(payload, settings.TOKEN_SECRET_KEY, settings.TOKEN_ALGORITHM)
        return {'ticket': ticket, 'expires_at': timezone.to_str(exp), 'ttl_seconds': VIEW_TICKET_TTL_SECONDS}

    @staticmethod
    def verify_view_ticket(ticket: str, *, site_id: int) -> bool:
        try:
            claims = jwt.decode(
                ticket,
                settings.TOKEN_SECRET_KEY,
                algorithms=[settings.TOKEN_ALGORITHM],
                options={'verify_exp': True},
            )
        except JWTError:
            return False
        return claims.get('typ') == _VIEW_TICKET_TYPE and claims.get('site_id') == site_id

    # ---------------- 公开表单访问票（Growth 落地页） ----------------

    @staticmethod
    def _require_form_site_available(site: Site | None, *, form_ref: str) -> Site:
        """校验可签发表单令牌的 Publish 权威状态。"""
        if (
            site is None
            or site.status != 'active'
            or PublishService.is_expired(site)
            or site.current_revision_id is None
        ):
            raise errors.NotFoundError(msg='落地页不存在或已下线')
        if (
            form_ref != GROWTH_LEAD_FORM_REF
            or site.source_app != 'growth'
            or site.kind != 'page'
            or site.platform_project_id is None
        ):
            raise errors.ForbiddenError(
                msg='站点表单未开启',
                data={'error_code': 'PUBLISH_FORM_CLOSED'},
            )
        return site

    @staticmethod
    async def issue_form_access_token(
        db: AsyncSession,
        *,
        slug: str,
        form_ref: str,
        view_ticket: str | None,
    ) -> dict[str, Any]:
        """在站点访问校验后签发绑定 site/revision/form/expiry 的短时令牌。"""
        site = PublishService._require_form_site_available(
            await PublishService.get_site_by_slug(db, slug=slug),
            form_ref=form_ref,
        )
        if site.visibility == 'private':
            raise errors.ForbiddenError(
                msg='私有站点不开放公开表单',
                data={'error_code': 'PUBLISH_PRIVATE_FORM_FORBIDDEN'},
            )
        if site.visibility == 'password' and not (
            view_ticket and PublishService.verify_view_ticket(view_ticket, site_id=site.id)
        ):
            raise errors.ForbiddenError(
                msg='口令站点必须先完成访问校验',
                data={'error_code': 'PUBLISH_FORM_VIEW_REQUIRED'},
            )
        if site.visibility not in {'public', 'unlisted', 'password'}:
            raise errors.ForbiddenError(
                msg='站点当前不可提交表单',
                data={'error_code': 'PUBLISH_FORM_VISIBILITY_FORBIDDEN'},
            )

        exp = timezone.now() + timedelta(seconds=FORM_ACCESS_TOKEN_TTL_SECONDS)
        payload = {
            'typ': _FORM_ACCESS_TOKEN_TYPE,
            'site_id': site.id,
            'revision_id': site.current_revision_id,
            'form_ref': form_ref,
            'owner_id': site.owner_id,
            'platform_project_id': str(site.platform_project_id),
            'exp': timezone.to_utc(exp).timestamp(),
        }
        token = jwt.encode(payload, settings.TOKEN_SECRET_KEY, settings.TOKEN_ALGORITHM)
        return {
            'form_access_token': token,
            'site_id': site.id,
            'revision_id': site.current_revision_id,
            'form_ref': form_ref,
            'expires_at': timezone.to_str(exp),
            'ttl_seconds': FORM_ACCESS_TOKEN_TTL_SECONDS,
        }

    @staticmethod
    async def resolve_form_access(
        db: AsyncSession,
        *,
        publish_ref: str,
        form_access_token: str,
    ) -> dict[str, Any]:
        """验签并按当前 Publish 状态解析公开表单的权威项目绑定。"""
        try:
            claims = jwt.decode(
                form_access_token,
                settings.TOKEN_SECRET_KEY,
                algorithms=[settings.TOKEN_ALGORITHM],
                options={'verify_exp': True},
            )
        except JWTError as exc:
            raise errors.ForbiddenError(
                msg='表单访问令牌无效或已过期',
                data={'error_code': 'PUBLISH_FORM_TOKEN_INVALID'},
            ) from exc
        form_ref = claims.get('form_ref')
        if claims.get('typ') != _FORM_ACCESS_TOKEN_TYPE or not isinstance(form_ref, str):
            raise errors.ForbiddenError(
                msg='表单访问令牌无效',
                data={'error_code': 'PUBLISH_FORM_TOKEN_INVALID'},
            )
        site = PublishService._require_form_site_available(
            await PublishService.get_site_by_slug(db, slug=publish_ref),
            form_ref=form_ref,
        )
        if claims.get('site_id') != site.id:
            raise errors.ForbiddenError(
                msg='表单访问令牌与站点不匹配',
                data={'error_code': 'PUBLISH_FORM_TOKEN_SITE_MISMATCH'},
            )
        if claims.get('revision_id') != site.current_revision_id:
            raise errors.ForbiddenError(
                msg='落地页版本已更新，请刷新后重试',
                data={'error_code': 'PUBLISH_FORM_TOKEN_REVISION_STALE'},
            )
        if (
            claims.get('owner_id') != site.owner_id
            or claims.get('platform_project_id') != str(site.platform_project_id)
        ):
            raise errors.ForbiddenError(
                msg='表单访问令牌绑定已变化',
                data={'error_code': 'PUBLISH_FORM_TOKEN_BINDING_STALE'},
            )
        return {
            'site_id': site.id,
            'revision_id': site.current_revision_id,
            'form_ref': form_ref,
            'owner_hasn_id': site.owner_id,
            'platform_project_id': str(site.platform_project_id),
            'visibility': site.visibility,
        }

    @staticmethod
    async def get_growth_site_status(
        db: AsyncSession,
        *,
        owner_id: str,
        platform_project_id: str | UUID,
        growth_project_id: str,
    ) -> dict[str, Any] | None:
        """按 Growth 云端来源 ID 查询唯一站点，并复验 Owner 与平台项目。"""
        site = (
            await db.execute(
                select(Site).where(
                    Site.owner_id == owner_id,
                    Site.source_app == 'growth',
                    Site.source_ref == growth_project_id,
                    Site.deleted_time.is_(None),
                )
            )
        ).scalar_one_or_none()
        if site is None:
            return None
        if str(site.platform_project_id) != str(platform_project_id):
            raise errors.ConflictError(
                msg='Growth 来源站点挂靠了其他平台项目',
                data={'error_code': 'PUBLISH_GROWTH_PROJECT_MISMATCH'},
            )
        return {
            'site_id': site.id,
            'resource_uri': f'hasn://publish/sites/{site.id}',
            'slug': site.slug,
            'title': site.title,
            'form_ref': 'growth-lead-v1',
            'status': site.status,
            'visibility': site.visibility,
            'current_revision_id': site.current_revision_id,
            'platform_project_id': str(site.platform_project_id),
            'updated_time': timezone.to_str(site.updated_time) if site.updated_time else None,
        }


publish_service = PublishService()
