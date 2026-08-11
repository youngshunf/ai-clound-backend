"""桌面端发布模块核心业务 service。

设计事实源：docs/产品与技术/技术设计/04-端侧与渠道/桌面端/发布与自动更新/01-总体设计.md。

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

import asyncio
import hashlib
import os
import re
import tempfile

from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from fastapi import HTTPException
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_release.model import AppRelease, ReleaseAsset, ReleaseBuild
from backend.app.hasn_release.schema.release import (
    HEADLESS_PLATFORM_TARGETS,
    REQUIRED_DESKTOP_PLATFORMS,
    BuildDetail,
    CiCallbackRequest,
    CiMultipartAbortRequest,
    CiMultipartCompleteRequest,
    CiMultipartInitRequest,
    CiMultipartInitResponse,
    CiMultipartPartResponse,
    CiUploadResponse,
    ConfirmReleaseTagRequest,
    GithubBuildRequest,
    HeadlessImageDetail,
    HeadlessImageRequest,
    LatestReleaseResponse,
    PrepareReleaseRequest,
    PublishReleaseRequest,
    ReleaseAssetDetail,
    ReleaseBatchResponse,
    ReleaseCommitInput,
    ReleaseDetail,
    TauriPlatformEntry,
    TauriUpdaterManifest,
    UpdateReleaseMetaRequest,
)
from backend.common.exception import errors
from backend.common.llm import LLMError, llm_client
from backend.core.conf import settings
from backend.plugin.s3.service.storage_service import StorageService
from backend.utils.timezone import timezone

_SEMVER_CORE = re.compile(r'^(\d+)\.(\d+)\.(\d+)')
_RELEASE_NOTES_MAX_CHARS = 500
_MAX_RELEASE_COMMITS = 5000
_RELEASE_COMMIT_CHUNK_CHARS = 12000
_RELEASE_MULTIPART_PART_BYTES = 8 * 1024 * 1024


class _HashingSizedStream:
    """边转发边计算摘要，并校验实际字节数与 Content-Length 一致。"""

    def __init__(self, source: AsyncIterable[bytes], expected_size: int) -> None:
        self._source = source
        self.expected_size = expected_size
        self.received_size = 0
        self._sha256 = hashlib.sha256()

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        async for chunk in self._source:
            if not chunk:
                continue
            self.received_size += len(chunk)
            if self.received_size > self.expected_size:
                raise errors.RequestError(msg='上传产物实际大小超过 Content-Length')
            self._sha256.update(chunk)
            yield chunk
        if self.received_size != self.expected_size:
            raise errors.RequestError(msg='上传产物实际大小与 Content-Length 不一致')

    @property
    def sha256(self) -> str:
        return self._sha256.hexdigest()


async def _single_chunk(data: bytes) -> AsyncIterator[bytes]:  # noqa: RUF029 - 适配异步上传协议
    yield data


async def _stage_release_upload(data: AsyncIterable[bytes], size: int) -> tuple[Path, str]:
    """把慢速请求流有界落盘并返回服务端计算的 SHA-256；失败时不遗留临时文件。"""
    file_descriptor, raw_path = tempfile.mkstemp(prefix='hasn-release-', suffix='.upload')
    path = Path(raw_path)
    file_obj = os.fdopen(file_descriptor, 'wb')
    checked_stream = _HashingSizedStream(data, size)
    try:
        async for chunk in checked_stream:
            await asyncio.to_thread(file_obj.write, chunk)
        await asyncio.to_thread(file_obj.flush)
        await asyncio.to_thread(os.fsync, file_obj.fileno())
    except Exception:
        await asyncio.to_thread(file_obj.close)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        raise
    await asyncio.to_thread(file_obj.close)
    return path, checked_stream.sha256


async def _public_release_object_size(download_url: str) -> int | None:
    """经公共 CDN HEAD 校验已完成对象；七牛写入凭据不一定有 HeadObject 权限。"""
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=True) as client:
            response = await client.head(download_url, headers={'Accept-Encoding': 'identity'})
            response.raise_for_status()
        raw_size = response.headers.get('content-length', '').strip()
        return int(raw_size) if raw_size.isdigit() else None
    except (httpx.HTTPError, OSError, ValueError):
        return None


def _semver_tuple(version: str) -> tuple[int, int, int]:
    """取 semver 主体 (major, minor, patch)，忽略 -beta/+build 后缀；非法回落 (0,0,0)。"""
    m = _SEMVER_CORE.match((version or '').strip().lstrip('vV'))
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _is_newer(candidate: str, current: str) -> bool:
    """candidate 是否比 current 新（仅比较 semver 主体）。"""
    return _semver_tuple(candidate) > _semver_tuple(current)


def _next_patch_version(versions: list[str]) -> str:
    """从已经分配过的最高 semver 生成下一个补丁版本。"""
    highest = max((_semver_tuple(version) for version in versions), default=(0, 0, 0))
    return f'{highest[0]}.{highest[1]}.{highest[2] + 1}'


def _is_rebuild_tag(version: str, release_tag: str | None) -> bool:
    """release_tag 是否为该版本的不可变重打 tag。"""
    return bool(
        re.fullmatch(
            rf'v{re.escape(version)}-rebuild\.\d+',
            (release_tag or '').strip(),
        )
    )


def _can_join_active_replace(*, requested_version: str, active_version: str) -> bool:
    """覆盖发布断点续跑只能加入同版本活动批次，不能穿透其它版本。"""
    return bool(requested_version) and requested_version == active_version


def _can_join_published_batch(
    *,
    requested_version: str,
    source_commit: str,
    published_version: str,
    published_source_commit: str,
) -> bool:
    """复用已发布批次：自动模式要求同提交，显式版本则以冻结 tag 为准。"""
    if requested_version:
        return requested_version == published_version
    return source_commit.lower() == published_source_commit.lower()


def _next_rebuild_tag(version: str, current_tag: str | None) -> str:
    """根据当前发布 tag 分配下一个不可变重打 tag。"""
    match = re.fullmatch(
        rf'v{re.escape(version)}-rebuild\.(\d+)',
        (current_tag or '').strip(),
    )
    generation = int(match.group(1)) + 1 if match else 1
    return f'v{version}-rebuild.{generation}'


def _ensure_commit_lock_transition(*, tag_status: str, locked_commit: str, confirmed_commit: str) -> None:
    """判定「confirm-tag 能否把批次的锁定 commit 前移到 confirmed_commit」。

    prepare 写入的 `source_commit` 只是**候选基线**：第一台机器随后要在 main 上补一个
    「把版本号写进源码」的提交，release tag 必须指向那个提交，源码里的版本才等于发布版本
    （`git show v{x.y.z}:Cargo.toml` 才对得上）。所以 tag 未 ready 之前允许前移锁定点。

    一旦 tag_status 变成 ready（已经有平台按它检出并构建过），基线即冻结：同一批次四平台
    必须同 commit，中途换基线会做出「版本号相同、内容不同」的包。
    """
    if tag_status == 'ready' and confirmed_commit.lower() != locked_commit.lower():
        raise errors.RequestError(
            msg=f'批次已锁定在 {locked_commit}，不接受改到 {confirmed_commit}；'
            f'需要换基线请作废批次后重新 prepare'
        )


def _normalize_release_notes(raw: str, *, max_chars: int = _RELEASE_NOTES_MAX_CHARS) -> str:
    """清理 LLM 输出并保留 Markdown 结构，确定性限制源码长度。"""
    text_value = (raw or '').strip()
    text_value = re.sub(
        r'\A```(?:markdown|md|text)?[^\n]*\n?',
        '',
        text_value,
        flags=re.IGNORECASE,
    )
    text_value = re.sub(r'\n?```\s*\Z', '', text_value)
    if len(text_value) >= 2 and (text_value[0], text_value[-1]) in {
        ('"', '"'),
        ('“', '”'),
    }:
        text_value = text_value[1:-1]
    lines = [
        re.sub(r'[ \t]+', ' ', line.strip())
        for line in text_value.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        if line.strip()
    ]
    normalized = '\n'.join(lines).strip()
    if len(normalized) <= max_chars:
        return normalized
    candidate = normalized[:max_chars].rstrip()
    last_line_start = candidate.rfind('\n') + 1
    sentence_end = max(candidate.rfind(mark) for mark in '。！？；')
    if sentence_end >= last_line_start:
        return candidate[: sentence_end + 1].rstrip()
    clause_end = max(candidate.rfind(mark) for mark in '，,、;')
    if clause_end >= last_line_start:
        return f'{candidate[:clause_end].rstrip()}。'
    if last_line_start > 0:
        return candidate[: last_line_start - 1].rstrip()
    return f'{candidate[: max_chars - 1].rstrip()}…'


def _should_generate_release_notes(status: str) -> bool:
    """同一发布批次只生成一次；失败状态允许下一台打包机器重试。"""
    return status != 'ready'


def _completed_platforms(
    required_platforms: list[str],
    asset_keys: set[tuple[str, str]],
) -> list[str]:
    """只有 installer 与 updater 都存在的平台才算完成。"""
    return sorted(
        platform
        for platform in required_platforms
        if (platform, 'installer') in asset_keys and (platform, 'updater') in asset_keys
    )


class ReleaseService:
    # --------- 云端发布批次（跨机器版本与 tag 单一事实源） ---------

    async def _get_public_release_candidates(
        self,
        db: AsyncSession,
        channel: str,
    ) -> list[AppRelease]:
        """返回公开消费候选：正式最新版、平台回退版本及兼容草稿批次。"""
        releases = list(
            (
                await db.execute(
                    select(AppRelease).where(
                        AppRelease.channel == channel,
                        AppRelease.status.in_(('draft', 'published')),
                    )
                )
            )
            .scalars()
            .all()
        )
        published = [release for release in releases if release.status == 'published']
        latest_published = [release for release in published if release.is_latest]
        published_head = max(
            latest_published or published,
            key=lambda release: _semver_tuple(release.version),
            default=None,
        )
        published_fallbacks = [
            release
            for release in published
            if published_head is not None
            and release.id != published_head.id
            and _semver_tuple(release.version) < _semver_tuple(published_head.version)
        ]
        partial_releases = [
            release
            for release in releases
            if release.status == 'draft'
            and release.tag_status == 'ready'
            and release.release_notes_status == 'ready'
            and bool(release.release_tag)
            and bool(release.completed_platforms)
        ]
        # 新版本允许任一平台先发布；把旧正式版也保留为候选，缺席的平台才能继续拿到
        # 自己上一版的 installer/updater，直到该平台把同一批次补齐。
        candidates = (
            partial_releases
            + ([published_head] if published_head is not None else [])
            + published_fallbacks
        )
        return sorted(candidates, key=lambda release: _semver_tuple(release.version), reverse=True)

    @staticmethod
    def _platform_is_public(release: AppRelease, platform_target: str) -> bool:
        """正式版全部公开；草稿只公开 installer/updater 已成套上传的平台。"""
        return release.status == 'published' or platform_target in set(release.completed_platforms or [])

    @staticmethod
    def _to_batch(release: AppRelease) -> ReleaseBatchResponse:
        if not release.release_tag or not release.source_commit:
            raise errors.ServerError(msg='发布批次缺少 release_tag/source_commit')
        return ReleaseBatchResponse(
            id=release.id,
            version=release.version,
            channel=release.channel,
            release_tag=release.release_tag,
            previous_release_tag=release.previous_release_tag,
            source_commit=release.source_commit,
            tag_status=release.tag_status,
            release_notes_status=release.release_notes_status,
            release_notes_md=release.release_notes_md,
            release_notes_error=release.release_notes_error,
            required_platforms=list(release.required_platforms or []),
            completed_platforms=list(release.completed_platforms or []),
            status=release.status,
            published_time=release.published_time,
        )

    async def prepare_release(
        self,
        db: AsyncSession,
        req: PrepareReleaseRequest,
    ) -> ReleaseBatchResponse:
        """创建或加入当前频道唯一的发布批次。

        PostgreSQL 事务级 advisory lock 保证两台打包机器并发请求时只增加一次 patch。
        已有草稿批次时忽略后来机器的 HEAD，统一返回云端锁定的 commit 与 tag；首个平台
        发布后，其它平台可按同一 source_commit 自动复用；若 main 已前进，则显式指定该版本，
        仍返回批次冻结的 commit 与 tag 构建，不把新提交混入旧版本。
        """
        channel = (req.channel or 'stable').strip()
        if channel not in ('stable', 'beta'):
            raise errors.RequestError(msg=f'非法 channel: {channel}')
        source_commit = req.source_commit.lower()

        await db.execute(
            text('SELECT pg_advisory_xact_lock(hashtext(:lock_key))'),
            {'lock_key': f'hasn_release:desktop:{channel}'},
        )
        requested_version = (req.requested_version or '').strip()
        active = (
            await db.execute(
                select(AppRelease)
                .where(
                    AppRelease.channel == channel,
                    AppRelease.status == 'draft',
                    AppRelease.release_tag.is_not(None),
                )
                .order_by(AppRelease.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if active is not None:
            if req.replace_existing:
                # 同版本覆盖发布已经建出草稿后，Windows/macOS 的断点续跑仍会携带
                # replace_existing。目标正是当前活动批次时必须幂等加入；只有试图
                # 穿透另一个活动版本去重打历史版本才拒绝。
                if _can_join_active_replace(
                    requested_version=requested_version,
                    active_version=active.version,
                ):
                    return self._to_batch(active)
                raise errors.RequestError(
                    msg=f'{channel} 频道已有进行中的 {active.version} 批次（{active.release_tag}），'
                    '请先补齐或作废该批次，再重打已发布版本'
                )
            # 后来的机器加入既有批次。显式指定了别的版本号时必须报错而不是静默沿用批次版本：
            # 同一批次四平台必须同版本同 commit，想换版本只能先补齐或作废这个批次。
            if requested_version and requested_version != active.version:
                raise errors.RequestError(
                    msg=f'{channel} 频道已有进行中的 {active.version} 批次（{active.release_tag}），'
                    f'无法按 {requested_version} 发布；请先补齐该批次，或作废后重新 prepare'
                )
            return self._to_batch(active)

        latest_published_batch = (
            await db.execute(
                select(AppRelease)
                .where(
                    AppRelease.channel == channel,
                    AppRelease.status == 'published',
                    AppRelease.is_latest.is_(True),
                    AppRelease.release_tag.is_not(None),
                    AppRelease.source_commit.is_not(None),
                )
                .order_by(AppRelease.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if (
            not req.replace_existing
            and latest_published_batch is not None
            and _can_join_published_batch(
                requested_version=requested_version,
                source_commit=source_commit,
                published_version=latest_published_batch.version,
                published_source_commit=str(latest_published_batch.source_commit),
            )
        ):
            return self._to_batch(latest_published_batch)

        if req.replace_existing:
            if not requested_version:
                raise errors.RequestError(msg='覆盖已发布版本时必须指定 requested_version')
            release = (
                await db.execute(
                    select(AppRelease).where(
                        AppRelease.channel == channel,
                        AppRelease.version == requested_version,
                    )
                )
            ).scalar_one_or_none()
            if release is None or release.status != 'published':
                raise errors.RequestError(
                    msg=f'{channel}/{requested_version} 不是已发布版本，不允许原位覆盖'
                )

            # 同一源码重试复用已分配的 rebuild tag，避免断点重跑不断增长代次。
            if (
                _is_rebuild_tag(release.version, release.release_tag)
                and (release.source_commit or '').lower() == source_commit
            ):
                return self._to_batch(release)

            previous_tag = release.release_tag
            release.release_tag = _next_rebuild_tag(release.version, previous_tag)
            release.previous_release_tag = previous_tag
            release.source_commit = source_commit
            release.tag_status = 'pending'
            release.tag_created_time = None
            release.required_platforms = list(REQUIRED_DESKTOP_PLATFORMS)
            release.release_commits = []
            # 同版本重打不改变用户可见更新说明，仅更新构建溯源和资产。
            release.release_notes_status = 'ready'
            release.release_notes_error = None
            await db.flush()
            return self._to_batch(release)

        versions = list(
            (await db.execute(select(AppRelease.version).where(AppRelease.channel == channel))).scalars().all()
        )
        if requested_version:
            # 指定版本只允许往上走：回退或重号会让 updater 把老包当新版推给已装机用户。
            highest = max((_semver_tuple(version) for version in versions), default=(0, 0, 0))
            if _semver_tuple(requested_version) <= highest:
                raise errors.RequestError(
                    msg=f'指定版本 {requested_version} 不高于 {channel} 频道历史最高版本 '
                    f'{".".join(str(part) for part in highest)}，版本号只能往上走'
                )
            version = requested_version
        else:
            version = _next_patch_version(versions)
        release_tag = f'v{version}'
        previous = latest_published_batch
        if previous is None:
            previous = (
                await db.execute(
                    select(AppRelease)
                    .where(
                        AppRelease.channel == channel,
                        AppRelease.status == 'published',
                        AppRelease.release_tag.is_not(None),
                    )
                    .order_by(AppRelease.published_time.desc().nullslast(), AppRelease.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        release = AppRelease(
            version=version,
            channel=channel,
            release_notes_md=None,
            release_notes_en_md=None,
            status='draft',
            is_latest=False,
            source='github',
            github_run_id=None,
            release_tag=release_tag,
            previous_release_tag=previous.release_tag if previous else None,
            source_commit=source_commit,
            tag_status='pending',
            tag_created_time=None,
            required_platforms=list(REQUIRED_DESKTOP_PLATFORMS),
            completed_platforms=[],
            release_commits=[],
            release_notes_status='pending',
            release_notes_error=None,
            published_time=None,
        )
        db.add(release)
        await db.flush()
        return self._to_batch(release)

    async def get_release_batch(self, db: AsyncSession, release_id: int) -> ReleaseBatchResponse:
        release = (await db.execute(select(AppRelease).where(AppRelease.id == release_id))).scalar_one_or_none()
        if release is None or not release.release_tag:
            raise errors.NotFoundError(msg='发布批次不存在')
        return self._to_batch(release)

    async def _resolve_remote_tag_commit(self, release_tag: str) -> str:
        """解析轻量或附注 tag 最终指向的 commit；无 REST token 时使用只读 deploy key。"""
        repo = (settings.RELEASE_GITHUB_REPO or '').strip()
        if not repo:
            raise errors.ServerError(msg='未配置 RELEASE_GITHUB_REPO，无法核验 release tag')
        token = (settings.RELEASE_GITHUB_TOKEN or '').strip()
        if not token:
            return await self._resolve_remote_tag_commit_via_ssh(repo, release_tag)
        headers = {
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'Authorization': f'Bearer {token}',
        }
        base_url = f'https://api.github.com/repos/{repo}'
        ref_url = f'{base_url}/git/ref/tags/{quote(release_tag, safe="")}'
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.get(ref_url, headers=headers)
            if response.status_code == 404:
                raise errors.RequestError(msg=f'远端 release tag 尚不存在：{release_tag}')
            if response.status_code != 200:
                raise errors.ServerError(msg=f'GitHub tag 核验失败 HTTP {response.status_code}: {response.text[:300]}')
            obj = (response.json() or {}).get('object') or {}
            for _ in range(3):
                object_type = str(obj.get('type') or '')
                object_sha = str(obj.get('sha') or '').lower()
                if object_type == 'commit' and object_sha:
                    return object_sha
                if object_type != 'tag' or not object_sha:
                    break
                tag_response = await client.get(f'{base_url}/git/tags/{object_sha}', headers=headers)
                if tag_response.status_code != 200:
                    raise errors.ServerError(
                        msg=f'GitHub 附注 tag 解析失败 HTTP {tag_response.status_code}: {tag_response.text[:300]}'
                    )
                obj = (tag_response.json() or {}).get('object') or {}
        raise errors.ServerError(msg=f'无法解析 release tag 指向的 commit：{release_tag}')

    async def _resolve_remote_tag_commit_via_ssh(
        self,
        repo: str,
        release_tag: str,
    ) -> str:
        """通过生产机现有 GitHub 只读 deploy key 校验私有仓 tag，不接触写权限。"""
        direct_ref = f'refs/tags/{release_tag}'
        peeled_ref = f'{direct_ref}^{{}}'
        env = os.environ.copy()
        env['GIT_TERMINAL_PROMPT'] = '0'
        env['GIT_SSH_COMMAND'] = 'ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes'
        try:
            process = await asyncio.create_subprocess_exec(
                'git',
                'ls-remote',
                '--tags',
                f'git@github.com:{repo}.git',
                direct_ref,
                peeled_ref,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            except TimeoutError:
                process.kill()
                await process.communicate()
                raise errors.ServerError(msg='GitHub SSH tag 校验超时') from None
        except FileNotFoundError:
            raise errors.ServerError(msg='生产环境缺少 git，无法通过 deploy key 校验 release tag') from None

        if process.returncode != 0:
            detail = stderr.decode('utf-8', errors='replace').strip()[:300]
            raise errors.ServerError(msg=f'GitHub SSH tag 校验失败：{detail or process.returncode}')

        refs: dict[str, str] = {}
        for raw_line in stdout.decode('utf-8', errors='replace').splitlines():
            parts = raw_line.split()
            if len(parts) == 2:
                refs[parts[1]] = parts[0].lower()
        commit = refs.get(peeled_ref) or refs.get(direct_ref)
        if not commit:
            raise errors.RequestError(msg=f'远端 release tag 尚不存在：{release_tag}')
        return commit

    async def _generate_release_notes(
        self,
        *,
        version: str,
        previous_release_tag: str | None,
        release_tag: str,
        commits: list[ReleaseCommitInput],
    ) -> str:
        """调用统一 LLM 客户端，把 Git 历史整理成 500 字以内 Markdown 更新说明。"""
        if not llm_client.is_configured:
            raise LLMError('统一 LLM 网关未配置')
        commit_lines: list[str] = []
        for commit in commits[:_MAX_RELEASE_COMMITS]:
            subject = re.sub(r'\s+', ' ', commit.subject).strip()
            commit_lines.append(f'- {commit.sha[:12]} {subject}')
        if not commit_lines:
            commit_lines = ['（该 tag 相比上一 release tag 没有新的 Git 提交）']

        chunks: list[str] = []
        current_lines: list[str] = []
        current_length = 0
        for line in commit_lines:
            if current_lines and current_length + len(line) + 1 > _RELEASE_COMMIT_CHUNK_CHARS:
                chunks.append('\n'.join(current_lines))
                current_lines = []
                current_length = 0
            current_lines.append(line)
            current_length += len(line) + 1
        if current_lines:
            chunks.append('\n'.join(current_lines))

        summary_source = chunks[0]
        if len(chunks) > 1:
            chunk_summaries: list[str] = []
            for index, chunk in enumerate(chunks, start=1):
                chunk_summary = await llm_client.complete(
                    [
                        {
                            'role': 'system',
                            'content': (
                                '你是版本历史整理助手。仅依据Git提交，合并重复项并提取用户可感知的'
                                '功能、修复、性能和安全变化；忽略merge、chore和纯工程噪音。'
                                '输出不超过400字的中文要点，不写commit哈希。'
                            ),
                        },
                        {
                            'role': 'user',
                            'content': f'提交分段 {index}/{len(chunks)}：\n{chunk}',
                        },
                    ],
                    max_tokens=800,
                    temperature=0.1,
                )
                normalized_chunk = _normalize_release_notes(chunk_summary, max_chars=400)
                chunk_summaries.append(re.sub(r'\s+', ' ', normalized_chunk))
            summary_source = '\n'.join(
                f'- 分段{index}：{summary}' for index, summary in enumerate(chunk_summaries, start=1)
            )
        notes = await llm_client.complete(
            [
                {
                    'role': 'system',
                    'content': (
                        '你是唤星AI桌面端的版本说明编辑。只能依据提供的Git提交事实，'
                        '合并重复内容，优先概括用户可感知的新功能、问题修复、性能与安全改进。'
                        '忽略merge、chore、版本号调整和纯工程噪音。输出可直接展示的Markdown：'
                        '不要标题、表格、代码块和链接；按实际内容写1至5条无序列表；'
                        '每条用 **新增**、**优化**、**修复**、**性能**、**安全** 中最贴切的标签开头，'
                        '格式如“- **新增**：支持……”。同类变化必须合并，不得补写提交中没有的事实。'
                        'Markdown源码总长度不超过500个字符，不写版本号、commit哈希、发布方式或“本机自动发布”。'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        f'版本：{version}\n'
                        f'范围：{previous_release_tag or "仓库最早可用release tag"}..{release_tag}\n'
                        f'Git历史{"分段摘要" if len(chunks) > 1 else "提交"}：\n{summary_source}'
                    ),
                },
            ],
            max_tokens=1000,
            temperature=0.2,
        )
        normalized = _normalize_release_notes(notes)
        if not normalized:
            raise LLMError('LLM 返回空更新说明')
        return normalized

    async def confirm_release_tag(
        self,
        db: AsyncSession,
        release_id: int,
        req: ConfirmReleaseTagRequest,
    ) -> ReleaseBatchResponse:
        """核验远端 tag，并幂等生成版本说明；LLM 失败时保留批次供原版本重试。"""
        await db.execute(
            text('SELECT pg_advisory_xact_lock(hashtext(:lock_key))'),
            {'lock_key': f'hasn_release:desktop:batch:{release_id}'},
        )
        release = (await db.execute(select(AppRelease).where(AppRelease.id == release_id))).scalar_one_or_none()
        if release is None or not release.release_tag or not release.source_commit:
            raise errors.NotFoundError(msg='发布批次不存在')

        confirmed_commit = req.source_commit.lower()
        _ensure_commit_lock_transition(
            tag_status=str(release.tag_status or ''),
            locked_commit=str(release.source_commit),
            confirmed_commit=confirmed_commit,
        )

        remote_commit = await self._resolve_remote_tag_commit(release.release_tag)
        if remote_commit != confirmed_commit:
            raise errors.RequestError(
                msg=f'远端 {release.release_tag} 指向 {remote_commit}，不是本机确认的 {confirmed_commit}'
            )
        release.source_commit = confirmed_commit
        release.tag_status = 'ready'
        release.tag_created_time = release.tag_created_time or timezone.now()

        normalized_commits = [
            {'sha': commit.sha.lower(), 'subject': re.sub(r'\s+', ' ', commit.subject).strip()}
            for commit in req.commits[:_MAX_RELEASE_COMMITS]
        ]
        if normalized_commits:
            release.release_commits = normalized_commits
        if req.previous_release_tag:
            release.previous_release_tag = req.previous_release_tag.strip()

        if _should_generate_release_notes(release.release_notes_status):
            release.release_notes_status = 'pending'
            release.release_notes_error = None
            try:
                release.release_notes_md = await self._generate_release_notes(
                    version=release.version,
                    previous_release_tag=release.previous_release_tag,
                    release_tag=release.release_tag,
                    commits=[ReleaseCommitInput.model_validate(commit) for commit in (release.release_commits or [])],
                )
                release.release_notes_status = 'ready'
            except LLMError as exc:
                release.release_notes_status = 'failed'
                release.release_notes_error = str(exc)[:1000]
        await db.flush()
        return self._to_batch(release)

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
            await db.execute(select(AppRelease).where(AppRelease.version == req.version, AppRelease.channel == channel))
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
        """CI 构建完成回调。

        新流程携 release_id：按平台幂等 upsert，任一平台完成即可发布，其它平台随后补齐。
        旧 CI 未携 release_id 时继续走原发布接口，保证存量 workflow 可用。
        """
        if req.release_id is not None:
            detail = await self._submit_batch_assets(db, req)
        else:
            detail = await self.publish(db, req, source='github')
        if req.build_id is not None:
            await self._set_build_status(
                db, req.build_id, status='success', version=req.version, run_id=req.github_run_id
            )
            await db.flush()  # 同上：外层事务自动提交，这里只 flush
        return detail

    async def _submit_batch_assets(
        self,
        db: AsyncSession,
        req: CiCallbackRequest,
    ) -> ReleaseDetail:
        """把单台机器产物写入发布批次，不删除其它平台已有资产。"""
        release = (await db.execute(select(AppRelease).where(AppRelease.id == req.release_id))).scalar_one_or_none()
        if release is None or not release.release_tag:
            raise errors.NotFoundError(msg='发布批次不存在')
        if req.version != release.version or req.channel != release.channel:
            raise errors.RequestError(
                msg=f'发布批次不匹配：云端={release.channel}/{release.version}，回调={req.channel}/{req.version}'
            )
        if req.release_tag and req.release_tag != release.release_tag:
            raise errors.RequestError(msg=f'release tag 不匹配：云端={release.release_tag}，回调={req.release_tag}')
        if release.status not in ('draft', 'published'):
            raise errors.RequestError(msg=f'发布批次状态不允许上传：{release.status}')
        if not req.assets:
            raise errors.RequestError(msg='至少需要一个发布资产')

        allowed_platforms = set(release.required_platforms or [])
        for asset in req.assets:
            if asset.platform_target not in allowed_platforms:
                raise errors.RequestError(msg=f'资产平台不属于当前发布批次：{asset.platform_target}')
            if asset.asset_kind not in ('installer', 'updater'):
                raise errors.RequestError(msg=f'非法 asset_kind: {asset.asset_kind}')
            if asset.asset_kind == 'updater' and not (asset.signature or '').strip():
                raise errors.RequestError(msg=f'updater 资产 {asset.platform_target} 缺 minisign 签名（拒收）')
            if not (asset.download_url or '').lower().startswith('https://'):
                raise errors.RequestError(
                    msg=f'资产 {asset.platform_target}/{asset.asset_kind} 下载地址必须是 https CDN 直链'
                )
            existing_asset = (
                await db.execute(
                    select(ReleaseAsset).where(
                        ReleaseAsset.release_id == release.id,
                        ReleaseAsset.platform_target == asset.platform_target,
                        ReleaseAsset.asset_kind == asset.asset_kind,
                    )
                )
            ).scalar_one_or_none()
            if existing_asset is None:
                db.add(
                    ReleaseAsset(
                        release_id=release.id,
                        platform_target=asset.platform_target,
                        asset_kind=asset.asset_kind,
                        download_url=asset.download_url,
                        file_name=asset.file_name,
                        file_size=asset.file_size or 0,
                        sha256=asset.sha256,
                        signature=asset.signature,
                        download_count=0,
                    )
                )
            else:
                existing_asset.download_url = asset.download_url
                existing_asset.file_name = asset.file_name
                existing_asset.file_size = asset.file_size or 0
                existing_asset.sha256 = asset.sha256
                existing_asset.signature = asset.signature
        await db.flush()

        keys = set(
            (
                await db.execute(
                    select(ReleaseAsset.platform_target, ReleaseAsset.asset_kind).where(
                        ReleaseAsset.release_id == release.id
                    )
                )
            )
            .tuples()
            .all()
        )
        release.completed_platforms = _completed_platforms(
            list(release.required_platforms or []),
            keys,
        )
        ready = (
            bool(release.completed_platforms)
            and release.tag_status == 'ready'
            and release.release_notes_status == 'ready'
        )
        if _is_rebuild_tag(release.version, release.release_tag):
            # 原位重打仅替换本次平台资产：发布可见性和 latest 指针均沿用原值。
            # 尤其不能因为重打历史版本，就把它重新提升为当前最新版。
            release.status = 'published'
        elif ready:
            await db.execute(
                update(AppRelease)
                .where(AppRelease.channel == release.channel, AppRelease.id != release.id)
                .values(is_latest=False)
            )
            release.status = 'published'
            release.is_latest = True
            release.published_time = release.published_time or timezone.now()
        else:
            release.status = 'draft'
            release.is_latest = False
        await db.flush()
        return await self.get_detail(db, release.id)

    async def ci_upload_asset(
        self,
        db: AsyncSession,
        *,
        data: bytes,
        filename: str,
        version: str,
        channel: str = 'stable',
        release_id: int | None = None,
        content_type: str | None = None,
    ) -> CiUploadResponse:
        """CI 把桌面端产物交云端入**公共桶**（复用云端既有七牛存储，CI 零额外凭据）。

        - 键：desktop/{channel}/{version}/{basename}，落 public 桶回长效 CDN 直链。
        - 强制 https：桌面端/官网走 ATS，http 直链会被拒（铁律：七牛 CDN 必须 https）。
        - sha256 由服务端据落桶字节现算，回给 CI 与其本地摘要对拍（零 fake，杜绝传输损坏静默）。
        """
        return await self.ci_upload_asset_stream(
            db,
            data=_single_chunk(data),
            size=len(data),
            filename=filename,
            version=version,
            channel=channel,
            release_id=release_id,
            content_type=content_type,
        )

    async def ci_upload_asset_stream(
        self,
        db: AsyncSession,
        *,
        data: AsyncIterable[bytes],
        size: int,
        filename: str,
        version: str,
        channel: str = 'stable',
        release_id: int | None = None,
        content_type: str | None = None,
    ) -> CiUploadResponse:
        """流式接收 CI 请求体，落临时文件后分片上传公共对象存储。"""
        if size <= 0:
            raise errors.RequestError(msg='上传产物不能为空')
        name = PurePosixPath((filename or '').strip()).name or 'asset.bin'  # 取 basename，杜绝路径穿越
        channel = (channel or 'stable').strip() or 'stable'
        version = (version or '').strip()
        if not version:
            raise errors.RequestError(msg='version 不能为空')
        if release_id is not None:
            release = (await db.execute(select(AppRelease).where(AppRelease.id == release_id))).scalar_one_or_none()
            if release is None or release.version != version or release.channel != channel:
                raise errors.RequestError(msg='上传目标与云端发布批次不匹配')
        object_key = f'desktop/{channel}/{version}/{name}'
        mime = content_type or 'application/octet-stream'
        storage = await StorageService.get_write_storage(db, access='public')
        # 上传可能持续很久；存储快照与批次校验完成后释放数据库连接，不把连接池槽位
        # 占到客户端慢速上传和七牛分片上传结束。
        await db.rollback()
        staged_path, sha256 = await _stage_release_upload(data, size)
        staged_file = None
        try:
            staged_file = await asyncio.to_thread(staged_path.open, 'rb')
            ref = await StorageService.upload_public_package_to_storage(
                storage,
                staged_file,
                size=size,
                key=object_key,
                content_type=mime,
            )
        finally:
            if staged_file is not None:
                await asyncio.to_thread(staged_file.close)
            await asyncio.to_thread(staged_path.unlink, missing_ok=True)
        if not ref.stable_url.startswith('https://'):
            raise errors.ServerError(msg=f'公共桶 CDN 非 https，桌面端 ATS 会拒下: {ref.stable_url}')
        return CiUploadResponse(
            download_url=ref.stable_url,
            file_name=name,
            file_size=ref.size,
            sha256=sha256,
            object_key=ref.object_key,
        )

    async def ci_multipart_init(
        self,
        db: AsyncSession,
        obj: CiMultipartInitRequest,
    ) -> CiMultipartInitResponse:
        """创建由 CI 逐片调用云端接口的对象存储 multipart 会话。"""
        name, object_key = await self._validate_ci_multipart_target(db, obj)
        del name
        storage = await StorageService.get_write_storage(db, access='public')
        upload_id = await StorageService.create_multipart_on_storage(
            storage,
            object_key=object_key,
            content_type=obj.content_type,
        )
        return CiMultipartInitResponse(
            upload_id=upload_id,
            object_key=object_key,
            part_size=_RELEASE_MULTIPART_PART_BYTES,
        )

    async def ci_multipart_upload_part(
        self,
        db: AsyncSession,
        *,
        obj: CiMultipartInitRequest,
        object_key: str,
        upload_id: str,
        part_number: int,
        data: AsyncIterable[bytes],
        size: int,
    ) -> CiMultipartPartResponse:
        """接收一个固定大小分片并立即写入对象存储；同序号重试会覆盖旧分片。"""
        _, expected_key = await self._validate_ci_multipart_target(db, obj)
        if object_key != expected_key:
            raise errors.RequestError(msg='multipart 对象键与发布目标不匹配')
        part_count = (obj.file_size + _RELEASE_MULTIPART_PART_BYTES - 1) // _RELEASE_MULTIPART_PART_BYTES
        if part_number < 1 or part_number > part_count:
            raise errors.RequestError(msg='multipart 分片序号超出文件范围')
        expected_size = min(
            _RELEASE_MULTIPART_PART_BYTES,
            obj.file_size - (part_number - 1) * _RELEASE_MULTIPART_PART_BYTES,
        )
        if size != expected_size:
            raise errors.RequestError(msg='multipart 分片大小与文件边界不匹配')
        storage = await StorageService.get_write_storage(db, access='public')
        await db.rollback()
        staged_path, _ = await _stage_release_upload(data, size)
        staged_file = None
        try:
            staged_file = await asyncio.to_thread(staged_path.open, 'rb')
            etag = await StorageService.upload_multipart_part_on_storage(
                storage,
                object_key=object_key,
                upload_id=upload_id,
                part_number=part_number,
                file=staged_file,
                size=size,
            )
        finally:
            if staged_file is not None:
                await asyncio.to_thread(staged_file.close)
            await asyncio.to_thread(staged_path.unlink, missing_ok=True)
        return CiMultipartPartResponse(part_number=part_number, etag=etag)

    async def ci_multipart_complete(
        self,
        db: AsyncSession,
        obj: CiMultipartCompleteRequest,
    ) -> CiUploadResponse:
        """提交全部分片，校验对象大小并返回与旧上传接口一致的结果。"""
        name, expected_key = await self._validate_ci_multipart_target(db, obj)
        if obj.object_key != expected_key:
            raise errors.RequestError(msg='multipart 对象键与发布目标不匹配')
        expected_count = (obj.file_size + _RELEASE_MULTIPART_PART_BYTES - 1) // _RELEASE_MULTIPART_PART_BYTES
        parts = [(part.part_number, part.etag) for part in obj.parts]
        if len(parts) != expected_count or [number for number, _ in parts] != list(range(1, expected_count + 1)):
            raise errors.RequestError(msg='multipart 分片清单不完整或顺序错误')
        storage = await StorageService.get_write_storage(db, access='public')
        await db.rollback()
        download_url = StorageService.public_url(storage, obj.object_key)
        if not download_url.startswith('https://'):
            raise errors.ServerError(msg=f'公共桶 CDN 非 https，桌面端 ATS 会拒下: {download_url}')
        # complete_multipart_upload 已在供应商侧成功、但随后 HEAD/响应链路瞬时失败时，
        # 客户端会用同一会话重试；此时供应商已删除 upload_id。先认领大小完全匹配的
        # CDN 对象，使完成接口具备幂等性。七牛写入 AK 没有 HeadObject 权限，因此不能
        # 用 S3 stat；公共 CDN HEAD 同时验证了桌面端最终实际消费的地址。
        completed_size = await _public_release_object_size(download_url)
        if completed_size != obj.file_size:
            await StorageService.complete_multipart_on_storage(
                storage,
                object_key=obj.object_key,
                upload_id=obj.upload_id,
                parts=parts,
            )
            for _ in range(10):
                completed_size = await _public_release_object_size(download_url)
                if completed_size == obj.file_size:
                    break
                await asyncio.sleep(1)
        if completed_size != obj.file_size:
            raise errors.ServerError(msg='multipart 完成后的对象大小与本地文件不一致')
        return CiUploadResponse(
            download_url=download_url,
            file_name=name,
            file_size=completed_size,
            sha256=obj.sha256,
            object_key=obj.object_key,
        )

    async def ci_multipart_abort(
        self,
        db: AsyncSession,
        obj: CiMultipartAbortRequest,
    ) -> None:
        """终止失败的发布 multipart 会话并清理对象存储残留分片。"""
        _, expected_key = await self._validate_ci_multipart_target(db, obj)
        if obj.object_key != expected_key:
            raise errors.RequestError(msg='multipart 对象键与发布目标不匹配')
        storage = await StorageService.get_write_storage(db, access='public')
        await db.rollback()
        await StorageService.abort_multipart_on_storage(
            storage,
            object_key=obj.object_key,
            upload_id=obj.upload_id,
        )

    @staticmethod
    async def _validate_ci_multipart_target(
        db: AsyncSession,
        obj: CiMultipartInitRequest,
    ) -> tuple[str, str]:
        """校验批次并只由服务端推导对象键，禁止客户端选择任意桶内路径。"""
        name = PurePosixPath(obj.file_name.strip()).name or 'asset.bin'
        channel = obj.channel.strip() or 'stable'
        version = obj.version.strip()
        if not version:
            raise errors.RequestError(msg='version 不能为空')
        if obj.release_id is not None:
            release = (await db.execute(select(AppRelease).where(AppRelease.id == obj.release_id))).scalar_one_or_none()
            if release is None or release.version != version or release.channel != channel:
                raise errors.RequestError(msg='上传目标与云端发布批次不匹配')
        return name, f'desktop/{channel}/{version}/{name}'

    # --------- 官网 / 桌面端消费 ---------

    async def get_latest(self, db: AsyncSession, channel: str = 'stable') -> LatestReleaseResponse:
        """返回当前最高版本说明，并为每个平台选择已经可用的最新 installer。"""
        releases = await self._get_public_release_candidates(db, channel)
        if not releases:
            return LatestReleaseResponse(channel=channel)

        release_ids = [release.id for release in releases]
        assets = list(
            (
                await db.execute(
                    select(ReleaseAsset).where(
                        ReleaseAsset.release_id.in_(release_ids),
                        ReleaseAsset.asset_kind == 'installer',
                    )
                )
            )
            .scalars()
            .all()
        )
        assets_by_release: dict[int, list[ReleaseAsset]] = {}
        for asset in assets:
            assets_by_release.setdefault(asset.release_id, []).append(asset)

        installers: dict[str, ReleaseAssetDetail] = {}
        platform_versions: dict[str, str] = {}
        for release in releases:
            for asset in assets_by_release.get(release.id, []):
                platform_target = asset.platform_target
                if platform_target in installers or not self._platform_is_public(release, platform_target):
                    continue
                installers[platform_target] = ReleaseAssetDetail.model_validate(asset)
                platform_versions[platform_target] = release.version

        headline = releases[0]
        return LatestReleaseResponse(
            version=headline.version,
            channel=channel,
            published_time=headline.published_time,
            release_notes_md=headline.release_notes_md,
            platform_versions=platform_versions,
            installers=installers,
        )

    async def build_updater_manifest(
        self, db: AsyncSession, *, target: str, arch: str, current_version: str, channel: str = 'stable'
    ) -> TauriUpdaterManifest | None:
        """Tauri updater manifest：仅当最新版本比 current 新时返回；否则 None（端点回 204）。

        target/arch 形如 darwin/aarch64 → platform_target=darwin-aarch64。
        """
        platform_target = f'{target}-{arch}'
        releases = await self._get_public_release_candidates(db, channel)
        eligible_releases = [
            release
            for release in releases
            if self._platform_is_public(release, platform_target) and _is_newer(release.version, current_version)
        ]
        if not eligible_releases:
            return None

        release_ids = [release.id for release in eligible_releases]
        updaters = list(
            (
                await db.execute(
                    select(ReleaseAsset).where(
                        ReleaseAsset.release_id.in_(release_ids),
                        ReleaseAsset.asset_kind == 'updater',
                        ReleaseAsset.platform_target == platform_target,
                    )
                )
            )
            .scalars()
            .all()
        )
        updater_by_release = {updater.release_id: updater for updater in updaters}
        selected: tuple[AppRelease, ReleaseAsset] | None = None
        for release in eligible_releases:
            updater = updater_by_release.get(release.id)
            if updater is not None and (updater.signature or '').strip():
                selected = (release, updater)
                break
        if selected is None:
            return None
        release, updater = selected

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
        asset = (await db.execute(select(ReleaseAsset).where(ReleaseAsset.id == asset_id))).scalar_one_or_none()
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
        self,
        db: AsyncSession,
        *,
        channel: str | None = None,
        limit: int = 50,
        offset: int = 0,
        published_only: bool = False,
    ) -> list[ReleaseDetail]:
        stmt = (
            select(AppRelease)
            .order_by(AppRelease.published_time.desc().nullslast(), AppRelease.id.desc())
            .offset(offset)
            .limit(limit)
        )
        if channel:
            stmt = stmt.where(AppRelease.channel == channel)
        if published_only:
            stmt = stmt.where(AppRelease.status == 'published')
        releases = (await db.execute(stmt)).scalars().all()
        result: list[ReleaseDetail] = [await self._to_detail(db, r) for r in releases]
        return result

    async def get_detail(self, db: AsyncSession, pk: int) -> ReleaseDetail:
        release = (await db.execute(select(AppRelease).where(AppRelease.id == pk))).scalar_one_or_none()
        if release is None:
            raise errors.NotFoundError(msg='版本不存在')
        return await self._to_detail(db, release)

    async def _to_detail(self, db: AsyncSession, release: AppRelease) -> ReleaseDetail:
        assets = (await db.execute(select(ReleaseAsset).where(ReleaseAsset.release_id == release.id))).scalars().all()
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
        await db.execute(update(AppRelease).where(AppRelease.channel == release.channel).values(is_latest=False))
        release.is_latest = True
        release.status = 'published'
        release.published_time = release.published_time or timezone.now()
        await db.flush()  # 外层事务自动提交，这里只 flush
        return await self.get_detail(db, pk)

    async def delete(self, db: AsyncSession, pk: int) -> None:
        release = (await db.execute(select(AppRelease).where(AppRelease.id == pk))).scalar_one_or_none()
        if release is None:
            raise errors.NotFoundError(msg='版本不存在')
        # 显式删除可让同一事务已加载的资产查询立即一致；FK CASCADE 仍承担库级兜底。
        await db.execute(delete(ReleaseAsset).where(ReleaseAsset.release_id == pk))
        await db.execute(delete(AppRelease).where(AppRelease.id == pk))
        await db.flush()  # 外层事务自动提交，这里只 flush

    # --------- CI 构建任务 ---------

    async def trigger_github_build(self, db: AsyncSession, req: GithubBuildRequest, *, actor: str) -> BuildDetail:
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
        except Exception as exc:
            build.status = 'failed'
            build.error_message = f'GitHub dispatch 异常: {exc}'
        await db.flush()  # 外层事务自动提交，这里只 flush
        return BuildDetail.model_validate(build)

    async def list_builds(self, db: AsyncSession, *, limit: int = 50) -> list[BuildDetail]:
        builds = (await db.execute(select(ReleaseBuild).order_by(ReleaseBuild.id.desc()).limit(limit))).scalars().all()
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

    # ─── 无头 hasn-node 容器镜像登记（H8） ───

    async def register_headless_image(self, db: AsyncSession, req: HeadlessImageRequest) -> HeadlessImageDetail:
        """登记一条无头镜像资产（契约 §7）。

        与桌面端流程**完全隔离**：只 upsert `(release, platform_target, asset_kind='image')` 一行，
        不删其它资产、不动 `is_latest` 指针、不改 `required_platforms`/`completed_platforms`。
        桌面端的 latest / updater manifest 都按 `asset_kind in (installer, updater)` 过滤，
        镜像行进不去那两条读路径。

        校验：
        - `platform_target` 必须是 headless 目标（写错就是往桌面端表里塞脏行）；
        - `image_digest` 必须是 `sha256:<64hex>`——digest 是滚动更新的唯一判据，格式不对即拒。
        """
        channel = req.channel or 'stable'
        if channel not in ('stable', 'beta'):
            raise errors.RequestError(msg=f'非法 channel: {channel}')
        if req.platform_target not in HEADLESS_PLATFORM_TARGETS:
            raise errors.RequestError(
                msg=f'非法 headless platform_target: {req.platform_target}（应为 {"/".join(HEADLESS_PLATFORM_TARGETS)}）'
            )
        digest = (req.image_digest or '').strip()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise errors.RequestError(msg='image_digest 必须形如 sha256:<64位小写hex>')
        image_ref = (req.image_ref or '').strip()
        if not image_ref:
            raise errors.RequestError(msg='image_ref 不能为空')

        release = (
            await db.execute(
                select(AppRelease).where(AppRelease.version == req.version, AppRelease.channel == channel)
            )
        ).scalar_one_or_none()
        now = timezone.now()
        if release is None:
            release = AppRelease(
                version=req.version,
                channel=channel,
                # 发布说明统一由下方「只填空缺」那一段写入，这里恒 None——
                # 否则新建分支先把值填上，那段判定会看到非空、把 release_notes_written 误报成 False。
                release_notes_md=None,
                release_notes_en_md=None,
                status='published' if req.publish else 'draft',
                is_latest=False,  # 镜像登记不抢桌面端 latest 指针
                source='github',
                github_run_id=None,
                published_time=now if req.publish else None,
            )
            db.add(release)
            await db.flush()
        elif req.publish and release.status == 'draft':
            release.status = 'published'
            release.published_time = release.published_time or now
        # 发布说明只填空缺、绝不覆盖：同版本号的桌面端发布链路可能已经维护了正文，
        # 无头镜像登记晚到时把它冲掉就是数据丢失。
        # `release_notes_written` 回显本次是否真写入——否则调用方只能声称「已提交」，
        # 无法区分「真写进去了」与「该版本已有正文被跳过」，那就是没有证据的声明。
        release_notes_written = bool(req.release_notes_md) and not release.release_notes_md
        if release_notes_written:
            release.release_notes_md = req.release_notes_md
        if req.min_cloud_contract_version is not None:
            release.min_cloud_contract_version = req.min_cloud_contract_version

        existing = (
            await db.execute(
                select(ReleaseAsset).where(
                    ReleaseAsset.release_id == release.id,
                    ReleaseAsset.platform_target == req.platform_target,
                    ReleaseAsset.asset_kind == 'image',
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                ReleaseAsset(
                    release_id=release.id,
                    platform_target=req.platform_target,
                    asset_kind='image',
                    download_url=image_ref,
                    file_name=image_ref.rsplit('/', 1)[-1],
                    file_size=req.image_size or 0,
                    sha256=digest,
                    signature=None,
                    download_count=0,
                )
            )
        else:
            existing.download_url = image_ref
            existing.file_name = image_ref.rsplit('/', 1)[-1]
            existing.file_size = req.image_size or 0
            existing.sha256 = digest
        # 写路由走 CurrentSessionTransaction（外层 begin() 自动提交），这里只 flush
        await db.flush()
        return HeadlessImageDetail(
            release_id=release.id,
            version=release.version,
            channel=release.channel,
            status=release.status,
            platform_target=req.platform_target,
            image_ref=image_ref,
            image_digest=digest,
            min_cloud_contract_version=release.min_cloud_contract_version,
            release_notes_written=release_notes_written,
        )


release_service = ReleaseService()
