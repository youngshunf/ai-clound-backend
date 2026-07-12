"""桌面端发布模块核心业务 service。

设计事实源：docs/hasn-node设计文档/桌面端发布与自动更新/00-桌面端发布与自动更新总体设计.md。

职责：
  - 发布/上传（管理端手动 + CI 回调共用 upsert 核心）：按 (version, channel) upsert 版本、
    替换资产、置 is_latest 指针（同 channel 旧版落 false）。
  - 官网/桌面端消费：latest（含各平台 installer）、Tauri updater manifest（版本比对 + 验签下发）、
    下载计数重定向解析。
  - CI 构建任务：创建/回填状态、触发 GitHub Actions workflow_dispatch（配置齐才真触发，缺配置诚实报错）。

签名策略（§9 校正）：二进制在七牛 CDN、不在云端，云端无法也不必对文件本体验签；
Tauri 客户端持公钥自行验签才是安全执行点。云端只**存储 + 下发** signature，
并对 updater 资产强制 signature 非空（缺签名的热更新包拒收）。
"""

from __future__ import annotations

import hashlib
import re

from pathlib import PurePosixPath

import httpx

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_release.model import AppRelease, ReleaseAsset, ReleaseBuild
from backend.app.hasn_release.schema.release import (
    BuildDetail,
    CiCallbackRequest,
    CiUploadResponse,
    GithubBuildRequest,
    LatestReleaseResponse,
    PublishReleaseRequest,
    ReleaseAssetDetail,
    ReleaseDetail,
    TauriPlatformEntry,
    TauriUpdaterManifest,
    UpdateReleaseMetaRequest,
)
from backend.common.exception import errors
from backend.core.conf import settings
from backend.plugin.s3.service.storage_service import StorageService
from backend.utils.timezone import timezone

_SEMVER_CORE = re.compile(r'^(\d+)\.(\d+)\.(\d+)')


def _semver_tuple(version: str) -> tuple[int, int, int]:
    """取 semver 主体 (major, minor, patch)，忽略 -beta/+build 后缀；非法回落 (0,0,0)。"""
    m = _SEMVER_CORE.match((version or '').strip().lstrip('vV'))
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _is_newer(candidate: str, current: str) -> bool:
    """candidate 是否比 current 新（仅比较 semver 主体）。"""
    return _semver_tuple(candidate) > _semver_tuple(current)


class ReleaseService:
    # --------- 发布 / 上传（管理端手动 + CI 回调共用核心） ---------

    async def publish(
        self,
        db: AsyncSession,
        req: PublishReleaseRequest,
        *,
        source: str,
    ) -> ReleaseDetail:
        """按 (version, channel) upsert 版本 + 替换资产 + 置 is_latest 指针。

        - source 由调用面决定（manual=管理端 / github=CI 回调），覆盖 req.source。
        - updater 资产强制 signature 非空（缺签名的热更新包拒收）。
        - set_latest=True：把同 channel 其它版本 is_latest 落 false，本版落 true + published。
        """
        channel = req.channel or 'stable'
        if channel not in ('stable', 'beta'):
            raise errors.RequestError(msg=f'非法 channel: {channel}')
        if not req.assets:
            raise errors.RequestError(msg='至少需要一个发布资产')

        # updater 资产必须携签名（客户端验签依据）
        for a in req.assets:
            if a.asset_kind not in ('installer', 'updater'):
                raise errors.RequestError(msg=f'非法 asset_kind: {a.asset_kind}')
            if a.asset_kind == 'updater' and not (a.signature or '').strip():
                raise errors.RequestError(
                    msg=f'updater 资产 {a.platform_target} 缺 minisign 签名（Tauri 客户端验签依据，拒收）'
                )
            if not (a.download_url or '').lower().startswith('https://'):
                raise errors.RequestError(msg=f'资产 {a.platform_target}/{a.asset_kind} 下载地址必须是 https CDN 直链')

        # upsert 版本行
        existing = (
            await db.execute(
                select(AppRelease).where(AppRelease.version == req.version, AppRelease.channel == channel)
            )
        ).scalar_one_or_none()

        now = timezone.now()
        if existing is None:
            release = AppRelease(
                version=req.version,
                channel=channel,
                release_notes_md=req.release_notes_md,
                release_notes_en_md=req.release_notes_en_md,
                status='published' if req.set_latest else 'draft',
                is_latest=False,  # 指针在下方统一置换
                source=source,
                github_run_id=req.github_run_id,
                published_time=now if req.set_latest else None,
            )
            db.add(release)
            await db.flush()
        else:
            release = existing
            release.release_notes_md = req.release_notes_md
            release.release_notes_en_md = req.release_notes_en_md
            release.source = source
            release.github_run_id = req.github_run_id
            if req.set_latest:
                release.status = 'published'
                release.published_time = release.published_time or now
            # 替换旧资产（幂等重发 / 补包）
            await db.execute(delete(ReleaseAsset).where(ReleaseAsset.release_id == release.id))
            await db.flush()

        # 写资产
        for a in req.assets:
            db.add(
                ReleaseAsset(
                    release_id=release.id,
                    platform_target=a.platform_target,
                    asset_kind=a.asset_kind,
                    download_url=a.download_url,
                    file_name=a.file_name,
                    file_size=a.file_size or 0,
                    sha256=a.sha256,
                    signature=a.signature,
                    download_count=0,
                )
            )

        # 置 is_latest 指针
        if req.set_latest:
            await db.execute(
                update(AppRelease)
                .where(AppRelease.channel == channel, AppRelease.id != release.id)
                .values(is_latest=False)
            )
            release.is_latest = True

        # 写路由走 CurrentSessionTransaction（外层 begin() 自动提交），这里只 flush，
        # 绝不 db.commit()——显式提交会关闭外层事务，后续 get_detail 读会炸「closed transaction」。
        await db.flush()
        return await self.get_detail(db, release.id)

    async def ci_callback(self, db: AsyncSession, req: CiCallbackRequest) -> ReleaseDetail:
        """CI 构建完成回调：source 固定 github；附带回填关联 build 状态为 success。"""
        detail = await self.publish(db, req, source='github')
        if req.build_id is not None:
            await self._set_build_status(
                db, req.build_id, status='success', version=req.version, run_id=req.github_run_id
            )
            await db.flush()  # 同上：外层事务自动提交，这里只 flush
        return detail

    async def ci_upload_asset(
        self,
        db: AsyncSession,
        *,
        data: bytes,
        filename: str,
        version: str,
        channel: str = 'stable',
        content_type: str | None = None,
    ) -> CiUploadResponse:
        """CI 把桌面端产物交云端入**公共桶**（复用云端既有七牛存储，CI 零额外凭据）。

        - 键：desktop/{channel}/{version}/{basename}，落 public 桶回长效 CDN 直链。
        - 强制 https：桌面端/官网走 ATS，http 直链会被拒（铁律：七牛 CDN 必须 https）。
        - sha256 由服务端据落桶字节现算，回给 CI 与其本地摘要对拍（零 fake，杜绝传输损坏静默）。
        """
        if not data:
            raise errors.RequestError(msg='上传产物不能为空')
        name = PurePosixPath((filename or '').strip()).name or 'asset.bin'  # 取 basename，杜绝路径穿越
        channel = (channel or 'stable').strip() or 'stable'
        version = (version or '').strip()
        if not version:
            raise errors.RequestError(msg='version 不能为空')
        object_key = f'desktop/{channel}/{version}/{name}'
        ref = await StorageService.upload(
            db,
            data,
            category='release_asset',
            filename=name,
            content_type=content_type or 'application/octet-stream',
            key=object_key,
        )
        if not ref.stable_url.startswith('https://'):
            raise errors.ServerError(msg=f'公共桶 CDN 非 https，桌面端 ATS 会拒下: {ref.stable_url}')
        return CiUploadResponse(
            download_url=ref.stable_url,
            file_name=name,
            file_size=ref.size,
            sha256=hashlib.sha256(data).hexdigest(),
            object_key=ref.object_key,
        )

    # --------- 官网 / 桌面端消费 ---------

    async def get_latest(self, db: AsyncSession, channel: str = 'stable') -> LatestReleaseResponse:
        """当前 channel 最新已发布版本 + 各平台 installer（官网 Hero + 下载页）。"""
        release = (
            await db.execute(
                select(AppRelease)
                .where(
                    AppRelease.channel == channel,
                    AppRelease.is_latest.is_(True),
                    AppRelease.status == 'published',
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if release is None:
            return LatestReleaseResponse(channel=channel)

        assets = (
            await db.execute(
                select(ReleaseAsset).where(
                    ReleaseAsset.release_id == release.id, ReleaseAsset.asset_kind == 'installer'
                )
            )
        ).scalars().all()
        installers = {a.platform_target: ReleaseAssetDetail.model_validate(a) for a in assets}
        return LatestReleaseResponse(
            version=release.version,
            channel=channel,
            published_time=release.published_time,
            release_notes_md=release.release_notes_md,
            installers=installers,
        )

    async def build_updater_manifest(
        self, db: AsyncSession, *, target: str, arch: str, current_version: str, channel: str = 'stable'
    ) -> TauriUpdaterManifest | None:
        """Tauri updater manifest：仅当最新版本比 current 新时返回；否则 None（端点回 204）。

        target/arch 形如 darwin/aarch64 → platform_target=darwin-aarch64。
        """
        platform_target = f'{target}-{arch}'
        release = (
            await db.execute(
                select(AppRelease)
                .where(
                    AppRelease.channel == channel,
                    AppRelease.is_latest.is_(True),
                    AppRelease.status == 'published',
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if release is None or not _is_newer(release.version, current_version):
            return None

        updater = (
            await db.execute(
                select(ReleaseAsset).where(
                    ReleaseAsset.release_id == release.id,
                    ReleaseAsset.asset_kind == 'updater',
                    ReleaseAsset.platform_target == platform_target,
                )
            )
        ).scalar_one_or_none()
        if updater is None or not (updater.signature or '').strip():
            return None

        pub_date = None
        if release.published_time is not None:
            pub_date = release.published_time.isoformat()
        return TauriUpdaterManifest(
            version=release.version,
            pub_date=pub_date,
            notes=release.release_notes_md,
            platforms={platform_target: TauriPlatformEntry(signature=updater.signature, url=updater.download_url)},
        )

    async def resolve_download(self, db: AsyncSession, asset_id: int) -> str:
        """下载计数重定向：累加 download_count，返回 CDN 直链（端点 302 跳转）。"""
        asset = (
            await db.execute(select(ReleaseAsset).where(ReleaseAsset.id == asset_id))
        ).scalar_one_or_none()
        if asset is None:
            raise HTTPException(status_code=404, detail='资产不存在')
        await db.execute(
            update(ReleaseAsset)
            .where(ReleaseAsset.id == asset_id)
            .values(download_count=ReleaseAsset.download_count + 1)
        )
        await db.commit()
        return asset.download_url

    # --------- 版本管理（管理端） ---------

    async def list_releases(
        self, db: AsyncSession, *, channel: str | None = None, limit: int = 50
    ) -> list[ReleaseDetail]:
        stmt = select(AppRelease).order_by(AppRelease.id.desc()).limit(limit)
        if channel:
            stmt = stmt.where(AppRelease.channel == channel)
        releases = (await db.execute(stmt)).scalars().all()
        result: list[ReleaseDetail] = []
        for r in releases:
            result.append(await self._to_detail(db, r))
        return result

    async def get_detail(self, db: AsyncSession, pk: int) -> ReleaseDetail:
        release = (await db.execute(select(AppRelease).where(AppRelease.id == pk))).scalar_one_or_none()
        if release is None:
            raise errors.NotFoundError(msg='版本不存在')
        return await self._to_detail(db, release)

    async def _to_detail(self, db: AsyncSession, release: AppRelease) -> ReleaseDetail:
        assets = (
            await db.execute(select(ReleaseAsset).where(ReleaseAsset.release_id == release.id))
        ).scalars().all()
        detail = ReleaseDetail.model_validate(release)
        detail.assets = [ReleaseAssetDetail.model_validate(a) for a in assets]
        return detail

    async def update_meta(self, db: AsyncSession, pk: int, req: UpdateReleaseMetaRequest) -> ReleaseDetail:
        release = (await db.execute(select(AppRelease).where(AppRelease.id == pk))).scalar_one_or_none()
        if release is None:
            raise errors.NotFoundError(msg='版本不存在')
        if req.release_notes_md is not None:
            release.release_notes_md = req.release_notes_md
        if req.release_notes_en_md is not None:
            release.release_notes_en_md = req.release_notes_en_md
        if req.status is not None:
            if req.status not in ('draft', 'published', 'deprecated'):
                raise errors.RequestError(msg=f'非法 status: {req.status}')
            release.status = req.status
            if req.status == 'deprecated' and release.is_latest:
                release.is_latest = False  # 下线即让出最新指针
        await db.flush()  # 外层事务自动提交，这里只 flush
        return await self.get_detail(db, pk)

    async def set_latest(self, db: AsyncSession, pk: int, channel: str = 'stable') -> ReleaseDetail:
        """把某历史版本重新置为最新（回滚 / 手动切换）。"""
        release = (await db.execute(select(AppRelease).where(AppRelease.id == pk))).scalar_one_or_none()
        if release is None:
            raise errors.NotFoundError(msg='版本不存在')
        await db.execute(
            update(AppRelease).where(AppRelease.channel == release.channel).values(is_latest=False)
        )
        release.is_latest = True
        release.status = 'published'
        release.published_time = release.published_time or timezone.now()
        await db.flush()  # 外层事务自动提交，这里只 flush
        return await self.get_detail(db, pk)

    async def delete(self, db: AsyncSession, pk: int) -> None:
        release = (await db.execute(select(AppRelease).where(AppRelease.id == pk))).scalar_one_or_none()
        if release is None:
            raise errors.NotFoundError(msg='版本不存在')
        # release_asset 经 FK ON DELETE CASCADE 随之清理
        await db.execute(delete(AppRelease).where(AppRelease.id == pk))
        await db.flush()  # 外层事务自动提交，这里只 flush

    # --------- CI 构建任务 ---------

    async def trigger_github_build(
        self, db: AsyncSession, req: GithubBuildRequest, *, actor: str
    ) -> BuildDetail:
        """创建构建任务行 + 触发 GitHub Actions workflow_dispatch。

        配置齐（token + repo + workflow）才真触发；缺配置则落 queued 行并写明原因（不 fake 成功）。
        """
        build = ReleaseBuild(
            ref=req.ref,
            channel=req.channel or 'stable',
            status='queued',
            version=None,
            github_run_id=None,
            github_run_url=None,
            triggered_by=actor,
            error_message=None,
        )
        db.add(build)
        await db.flush()

        token = (settings.RELEASE_GITHUB_TOKEN or '').strip()
        repo = (settings.RELEASE_GITHUB_REPO or '').strip()
        workflow = (settings.RELEASE_GITHUB_WORKFLOW or '').strip()
        if not (token and repo and workflow):
            build.error_message = '未配置 RELEASE_GITHUB_TOKEN/REPO/WORKFLOW，已排队但未触发 GitHub 构建'
            await db.flush()  # 外层事务自动提交，这里只 flush
            return BuildDetail.model_validate(build)

        url = f'https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches'
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    url,
                    headers={
                        'Authorization': f'Bearer {token}',
                        'Accept': 'application/vnd.github+json',
                        'X-GitHub-Api-Version': '2022-11-28',
                    },
                    json={'ref': req.ref, 'inputs': {'channel': req.channel or 'stable'}},
                )
            if resp.status_code in (201, 204):
                build.status = 'building'
                build.github_run_url = f'https://github.com/{repo}/actions'
            else:
                build.status = 'failed'
                build.error_message = f'GitHub dispatch 失败 HTTP {resp.status_code}: {resp.text[:300]}'
        except Exception as exc:  # noqa: BLE001
            build.status = 'failed'
            build.error_message = f'GitHub dispatch 异常: {exc}'
        await db.flush()  # 外层事务自动提交，这里只 flush
        return BuildDetail.model_validate(build)

    async def list_builds(self, db: AsyncSession, *, limit: int = 50) -> list[BuildDetail]:
        builds = (
            await db.execute(select(ReleaseBuild).order_by(ReleaseBuild.id.desc()).limit(limit))
        ).scalars().all()
        return [BuildDetail.model_validate(b) for b in builds]

    async def _set_build_status(
        self,
        db: AsyncSession,
        build_id: int,
        *,
        status: str,
        version: str | None = None,
        run_id: str | None = None,
        error: str | None = None,
    ) -> None:
        values: dict = {'status': status}
        if version is not None:
            values['version'] = version
        if run_id is not None:
            values['github_run_id'] = run_id
        if error is not None:
            values['error_message'] = error
        await db.execute(update(ReleaseBuild).where(ReleaseBuild.id == build_id).values(**values))


release_service = ReleaseService()
