"""设计系统生成应用（app_id=designsystem）云端业务服务。

云端是**权威存储**：确定性 token 契约引擎（compile/derive/validate/extract）在 daemon 本地 Rust
crate 跑，云端不重算，只存分身/人算好的 tokens.css + 派生产物 + 评分报告，并维护版本与同步水位。

可见域（list_visible）：builtin ∪ owner ∪ 企业（共享 ACL 在 P9 接 resource_share 补齐）。
owner 隔离：写操作强制 owner_hasn_id == subject.owner_hasn_id；builtin（is_builtin）跨 owner 只读。

同步水位（designsystem_revision）：owner 维度 content-hash 聚合（照搬 SKAU/PDC 范式，按需计算、
无独立存储），任一可见 design_system 的 content_hash 变化即变；daemon 据此增量重拉（P5 WSPUSH）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select

from backend.app.hasn.service.authz import Subject  # G6：收编来源，模块级再导出（既有调用点不变）
from backend.app.hasn.service.resource_share_service import rank, resource_share_service
from backend.app.hasn_designsystem.core.scenes import (
    DEFAULT_REQUIRED_SCENES,
    SCENE_STANDARDS,
    is_known_scene,
)
from backend.app.hasn_designsystem.model import Collaborator, DesignSystem, Revision
from backend.common.exception import errors
from backend.utils.timezone import timezone

log = logging.getLogger(__name__)

# 共享产物类型（统一 resource_share 表里的 designsystem 命名空间，与 deck/doc/knowledge 并列）。
RESOURCE_TYPE = 'designsystem'

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# builtin（官方内置）设计系统的归属 owner 哨兵：对所有 owner 只读可见、不属任何真实 owner。
BUILTIN_OWNER = 'system'

# 进 content_hash 与 revision 的内容字段（真源 + 派生 + 创意）。
_REVISION_CONTENT = (
    'tokens_css',
    'design_tokens_json',
    'tailwind_css',
    'design_md',
    'components_html',
    'components_manifest_json',
    'token_contract_report_json',
)


def _content_hash(payload: dict[str, Any]) -> str:
    """对一版内容算确定性 sha256（与 daemon 镜像比对触发增量重拉）。"""
    canonical = {k: payload.get(k) for k in _REVISION_CONTENT}
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


# DSFIX-1：设计系统「完成」判定的必填内容字段 = 详情页四区块渲染所需（缺任一详情就空）。
# tokens.css（真源/色板）+ 契约评分报告（ScoreRing/色板分层）+ 设计说明（design.md）
# + 组件画廊 HTML + 组件清单 JSON。分身写满这五项 = 完整 → 首次完整发完成卡（非 runtime 自动完成）。
_REQUIRED_CONTENT_FIELDS = (
    'tokens_css',
    'token_contract_report_json',
    'design_md',
    'components_html',
    'components_manifest_json',
)


def _content_complete(content: dict[str, Any]) -> bool:
    """一版内容是否「完整」= 必填字段全部非空（决定分身完成发卡时机）。

    零造假：只认真实非空内容——None / 空串（strip 后空）/ 空 list/dict 都算缺；任一缺失即未完整
    （不发完成卡，详情仍会空，符合「必填齐了才发卡」）。
    """
    for key in _REQUIRED_CONTENT_FIELDS:
        val = content.get(key)
        if val is None:
            return False
        if isinstance(val, str) and not val.strip():
            return False
        if isinstance(val, (list, dict)) and not val:
            return False
    return True


# 预览色板：从 tokens.css 抽这几个 token 作列表卡迷你 mockup 的色（key → tokens.css 变量名）。
_PREVIEW_TOKENS = {
    'bg': '--bg',
    'surface': '--surface',
    'fg': '--fg',
    'muted': '--muted',
    'border': '--border',
    'accent': '--accent',
    'accent_on': '--accent-on',
}
# 仅取顶层 :root 里 `--name: value;` 的简单声明（确定性正则，不引 CSS 解析器；值取到分号前并 strip）。
_CSS_VAR_RE = re.compile(r'(--[a-z0-9-]+)\s*:\s*([^;]+);')


def _extract_preview_swatches(tokens_css: str | None) -> dict[str, str] | None:
    """从 tokens.css denorm 出预览色板（{bg,surface,fg,muted,border,accent,accent_on}）。

    供列表卡渲染迷你预览图，免逐项取产物。缺 tokens.css 或一个关键 token 都没命中 → None（零 fake，
    不编造颜色）；命中部分则只带命中的键（前端按缺失降级）。值原样保留（含 #hex / oklch / rgb 等）。
    """
    if not tokens_css:
        return None
    found = {name: value.strip() for name, value in _CSS_VAR_RE.findall(tokens_css)}
    swatches = {key: found[var] for key, var in _PREVIEW_TOKENS.items() if found.get(var)}
    return swatches or None


# DSGAL：场景标准（中文名 + 每个场景的必须组件带中文名），供完成卡软提示交叉用。
# 单一事实源在 core/scenes.py（与 Rust 逐字节对齐）；这里只 denorm 出 label 映射，不重复定义标准。
_SCENE_LABELS: dict[str, str] = {s.id: s.label for s in SCENE_STANDARDS}
_SCENE_REQUIRED: dict[str, list[tuple[str, str]]] = {
    s.id: [(c.key, c.label) for c in s.required] for s in SCENE_STANDARDS
}


def _normalize_required_scenes(raw: Any) -> list[str]:
    """规整 required_scenes 入参：只保留已知场景 id、去重保序；空/非法 → 默认 [brand_website]。

    零 fake：未知场景 id 直接丢弃（不臆造），空列表回落默认（画廊至少要求品牌网站）。
    """
    if not isinstance(raw, (list, tuple)):
        return list(DEFAULT_REQUIRED_SCENES)
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and is_known_scene(item) and item not in seen:
            seen.add(item)
            out.append(item)
    return out or list(DEFAULT_REQUIRED_SCENES)


def _scene_coverage_annotation(required_scenes: list[str], manifest_scenes: Any) -> list[dict[str, Any]]:
    """交叉 owner 要求的 required_scenes 与 manifest 检测到的 scenes[] → 每个必须场景的覆盖标注。

    软提示（不阻断）：为每个 required 场景算「必须 N 件 / 已配齐 M 件 / 缺哪几件（带中文名）」。
    manifest 未检测到某场景（分身一件没标）→ 视为该场景全部缺失（诚实反映实际产出）。
    """
    by_id: dict[str, dict[str, Any]] = {}
    if isinstance(manifest_scenes, list):
        for s in manifest_scenes:
            if isinstance(s, dict) and isinstance(s.get('id'), str):
                by_id[s['id']] = s
    out: list[dict[str, Any]] = []
    for scene_id in required_scenes:
        required_pairs = _SCENE_REQUIRED.get(scene_id)
        if required_pairs is None:  # 理论上 required_scenes 已规整过，防御性跳过未知
            continue
        detected = by_id.get(scene_id) or {}
        present_keys = set(detected.get('presentComponents') or [])
        required_keys = [k for k, _ in required_pairs]
        present = [k for k in required_keys if k in present_keys]
        missing = [(k, label) for k, label in required_pairs if k not in present_keys]
        out.append({
            'id': scene_id,
            'label': _SCENE_LABELS.get(scene_id, scene_id),
            'requiredTotal': len(required_keys),
            'presentCount': len(present),
            'missing': [{'key': k, 'label': label} for k, label in missing],
            'complete': not missing,
        })
    return out


def _scene_coverage_hint(annotation: list[dict[str, Any]]) -> str | None:
    """把覆盖标注压成一行软提示文案：「品牌网站 3/5 · 缺 CTA/页脚；移动端 0/5 · 缺 …」。

    全部场景配齐 → None（不提示，卡片不带累赘）。
    """
    parts: list[str] = []
    for a in annotation:
        if a.get('complete'):
            continue
        miss = '/'.join(m['label'] for m in a.get('missing', []))
        parts.append(f'{a["label"]} {a["presentCount"]}/{a["requiredTotal"]} · 缺 {miss}')
    return '；'.join(parts) or None


def _manifest_scenes_of(content: dict[str, Any]) -> Any:
    """从 save 入参 content 里取 components.manifest 的 scenes[]（daemon/Python core 抽取器已填）。

    manifest 存为 JSONB → 取到即 dict；非 dict（缺失/畸形）→ 返回 None（annotation 侧当全缺处理）。
    """
    manifest = content.get('components_manifest_json')
    if isinstance(manifest, dict):
        return manifest.get('scenes')
    return None


def _ds_dict(d: DesignSystem) -> dict[str, Any]:
    return {
        'id': d.id,
        'owner_hasn_id': d.owner_hasn_id,
        'name': d.name,
        'slug': d.slug,
        'category': d.category,
        'source_kind': d.source_kind,
        'score': d.score,
        'grade': d.grade,
        'recommend_rebuild': d.recommend_rebuild,
        'is_builtin': d.is_builtin,
        'enterprise_id': d.enterprise_id,
        'current_revision_id': d.current_revision_id,
        'content_hash': d.content_hash,
        'bound_agent_id': d.bound_agent_id,
        'preview_swatches': d.preview_swatches,
        # DSGAL：owner 要求覆盖的场景（详情页与之交叉 manifest.scenes 渲染覆盖分区）。存量 null → 规整默认。
        'required_scenes': _normalize_required_scenes(d.required_scenes),
        'completed_notified_at': d.completed_notified_at.isoformat() if d.completed_notified_at else None,
        'created_time': d.created_time.isoformat() if d.created_time else None,
        'updated_time': d.updated_time.isoformat() if d.updated_time else None,
    }


def _revision_dict(r: Revision, *, with_content: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        'id': r.id,
        'design_system_id': r.design_system_id,
        'rev_no': r.rev_no,
        'author_kind': r.author_kind,
        'author_id': r.author_id,
        'bundle_asset_id': r.bundle_asset_id,
        'note': r.note,
        'created_time': r.created_time.isoformat() if r.created_time else None,
    }
    if with_content:
        out.update(
            {
                'tokens_css': r.tokens_css,
                'design_tokens_json': r.design_tokens_json,
                'tailwind_css': r.tailwind_css,
                'design_md': r.design_md,
                'components_html': r.components_html,
                'components_manifest_json': r.components_manifest_json,
                'token_contract_report_json': r.token_contract_report_json,
            }
        )
    return out


class DesignSystemService:
    """设计系统云端服务：save（落 revision + bump）、list/get（可见域）、协作分身绑定。"""

    @staticmethod
    async def _get_alive(db: AsyncSession, design_system_id: int) -> DesignSystem:
        d = await db.get(DesignSystem, design_system_id)
        if d is None or d.deleted_time is not None:
            raise errors.NotFoundError(msg='设计系统不存在')
        return d

    @staticmethod
    def _readable_fast(d: DesignSystem, viewer_owner_hasn_id: str, *, enterprise_id: int | None = None) -> bool:
        """快路径可读：builtin / owner / 同企业。命中即可读，无需查 resource_share。"""
        if d.is_builtin:
            return True
        if d.owner_hasn_id == viewer_owner_hasn_id:
            return True
        return bool(enterprise_id is not None and d.enterprise_id == enterprise_id)

    async def _assert_can_read(
        self, db: AsyncSession, d: DesignSystem, viewer_owner_hasn_id: str, *, enterprise_id: int | None = None
    ) -> None:
        """可读判定（P9）：快路径 builtin/owner/企业；否则查 resource_share 显式共享（viewer↑）。"""
        if self._readable_fast(d, viewer_owner_hasn_id, enterprise_id=enterprise_id):
            return
        perm = await resource_share_service.resolve_effective_permission(
            db,
            subject_hasn_id=viewer_owner_hasn_id,
            subject_kind='human',
            subject_owner_hasn_id=viewer_owner_hasn_id,
            resource_type=RESOURCE_TYPE,
            resource_id=str(d.id),
            resource_owner_hasn_id=d.owner_hasn_id,
            resource_enterprise_id=d.enterprise_id,
        )
        if rank(perm) >= rank('viewer'):
            return
        raise errors.ForbiddenError(msg='无权访问该设计系统')

    async def save(
        self,
        db: AsyncSession,
        *,
        subject: Subject,
        design_system_id: int | None,
        slug: str,
        name: str,
        content: dict[str, Any],
        category: str | None = None,
        source_kind: str = 'generated',
        score: int | None = None,
        grade: str | None = None,
        recommend_rebuild: bool = False,
        bundle_asset_id: str | None = None,
        note: str | None = None,
        enterprise_id: int | None = None,
        required_scenes: list[str] | None = None,
    ) -> dict[str, Any]:
        """创建或更新一套设计系统：落一版 revision + 回填 current/content_hash/评分。

        `content` 含 tokens.css + 派生 + 创意（见 _REVISION_CONTENT）。每次 save 都出一版（可回滚）。
        `required_scenes`：owner 派发时要求覆盖的组件画廊场景（None=不改，新建时回落默认 [brand_website]）。
        """
        owner = subject.owner_hasn_id
        hashed = _content_hash(content)
        now = timezone.now()

        if design_system_id is None:
            # 新建：slug 在 owner 维度唯一（撞 slug → 视为更新已存在的同 slug 行）
            existing = (
                await db.execute(
                    select(DesignSystem).where(
                        DesignSystem.owner_hasn_id == owner,
                        DesignSystem.slug == slug,
                        DesignSystem.deleted_time.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                d = existing
            else:
                d = DesignSystem(
                    owner_hasn_id=owner,
                    name=name,
                    slug=slug,
                    category=category,
                    source_kind=source_kind,
                    enterprise_id=enterprise_id,
                    content_hash='',
                )
                db.add(d)
                await db.flush()
        else:
            d = await self._get_alive(db, design_system_id)
            if d.is_builtin:
                raise errors.ForbiddenError(msg='无权修改内置设计系统')
            if d.owner_hasn_id != owner:
                # 非 owner → 必须是有 editor↑ 权限的协作方（D6 协作分身改 tokens）。
                perm = await resource_share_service.resolve_effective_permission(
                    db,
                    subject_hasn_id=subject.hasn_id,
                    subject_kind=subject.kind,
                    subject_owner_hasn_id=subject.owner_hasn_id,
                    resource_type=RESOURCE_TYPE,
                    resource_id=str(d.id),
                    resource_owner_hasn_id=d.owner_hasn_id,
                    resource_enterprise_id=d.enterprise_id,
                )
                if rank(perm) < rank('editor'):
                    raise errors.ForbiddenError(msg='无权修改该设计系统')

        # AppCollab（doc21 / 实施21 AC-P3）：创建即绑定生成它的分身（DECKBIND 同模型）。
        # bind-only-if-unbound——未绑定且作者是分身 → 绑该分身；已绑定不因再次 save 静默改绑
        # （改绑只走 owner 二次确认 set_bound_agent）。owner 本人 save 不绑（无分身可绑）。
        if d.bound_agent_id is None and subject.kind == 'agent':
            d.bound_agent_id = subject.hasn_id

        # D6：owner（或其分身）改 → 直接落当前版；协作方（editor）改 → 出新版「待 owner 确认」，
        # 不动 owner 当前态（保留/丢弃由 owner 经 set_current_revision 裁决）。
        advance_current = design_system_id is None or d.owner_hasn_id == owner

        # 根字段更新（白名单：不允许改 owner/slug/is_builtin）——仅「落当前版」时生效。
        if advance_current:
            d.name = name
            if category is not None:
                d.category = category
            d.source_kind = source_kind
            d.score = score
            d.grade = grade
            d.recommend_rebuild = recommend_rebuild
            d.content_hash = hashed
            # 列表卡预览色板：denorm 当前版 tokens.css 关键色，前端列表渲染迷你预览（无 tokens → None）。
            d.preview_swatches = _extract_preview_swatches(content.get('tokens_css'))
            # DSGAL：required_scenes 显式传了才改（owner 派发时设定的场景要求；None=沿用存量/默认，
            # 避免分身每次 save 无意抹掉 owner 的场景勾选）。
            if required_scenes is not None:
                d.required_scenes = _normalize_required_scenes(required_scenes)
        d.updated_time = now

        # 落新版 revision（rev_no = 当前 max + 1）
        max_rev = (
            await db.execute(
                select(func.coalesce(func.max(Revision.rev_no), 0)).where(Revision.design_system_id == d.id)
            )
        ).scalar_one()
        rev = Revision(
            design_system_id=d.id,
            rev_no=int(max_rev) + 1,
            author_kind=subject.kind,
            author_id=subject.hasn_id,
            bundle_asset_id=bundle_asset_id,
            note=note,
            tokens_css=content.get('tokens_css'),
            design_tokens_json=content.get('design_tokens_json'),
            tailwind_css=content.get('tailwind_css'),
            design_md=content.get('design_md'),
            components_html=content.get('components_html'),
            components_manifest_json=content.get('components_manifest_json'),
            token_contract_report_json=content.get('token_contract_report_json'),
        )
        db.add(rev)
        await db.flush()
        if advance_current:
            d.current_revision_id = rev.id
        await db.commit()
        await db.refresh(d)

        # DSFIX-1：分身写满必填字段 → 首次完整 → 发一次「设计系统已完成·查看」卡给主人（幂等 + best-effort）。
        # 时机不是 runtime 自动完成，而是「必填字段齐了」（福仔铁律）。仅落当前版（advance_current）且作者是
        # 分身、且从未发过（completed_notified_at 为空）、且本版内容完整才发。发卡失败不阻断保存。
        should_notify = (
            advance_current
            and subject.kind == 'agent'
            and d.completed_notified_at is None
            and _content_complete(content)
        )
        if should_notify:
            # DSGAL：软提示——交叉 owner 要求的 required_scenes 与本版 manifest.scenes[]，算「品牌网站 3/5 ·
            # 缺 CTA/页脚」。不阻断发卡（完成判定见上 should_notify，只看五项必填字段）。
            scene_coverage = _scene_coverage_annotation(
                _normalize_required_scenes(d.required_scenes), _manifest_scenes_of(content)
            )
            try:
                await self._notify_designsystem_ready(
                    db, d=d, agent_hasn_id=subject.hasn_id, scene_coverage=scene_coverage
                )
                d.completed_notified_at = timezone.now()
                await db.commit()
                await db.refresh(d)
            except Exception:  # noqa: BLE001 — 完成卡是次要副作用，绝不因它回滚已保存的设计系统
                await db.rollback()
                await db.refresh(d)
                log.exception('设计系统完成卡发送失败（不阻断保存）design_system_id=%s', d.id)

        out = _ds_dict(d)
        out['revision'] = _revision_dict(rev, with_content=False)
        out['pending'] = not advance_current  # True=协作待确认版（未落当前态）
        return out

    async def _notify_designsystem_ready(
        self,
        db: AsyncSession,
        *,
        d: DesignSystem,
        agent_hasn_id: str,
        scene_coverage: list[dict[str, Any]] | None = None,
    ) -> None:
        """分身写满必填字段 → 发「设计系统已完成·查看」卡给主人（DSFIX-1）。

        source.kind=agent（category=agent 默认开 card_message → 卡片落「主人 ⇄ 该分身」既有会话）；
        深链走 `hasn://designsystem/{云端权威 id}`（相对 `/designsystem/{id}` 由 carrier 提升为 canonical，
        `{id}` 用云端权威 `d.id`——分享/换设备均可解析，符合「本地 ID 永不上 URI」铁律）。
        `scene_coverage`（DSGAL）：每个 required 场景的组件画廊覆盖标注，压成一行软提示挂进 preview，
        卡片诚实透出「品牌网站 3/5 · 缺 CTA/页脚」——软提示，不阻断（发卡时机仍只看五项必填字段）。
        惰性 import notification_service，避免模块加载期循环（与 _fanout_card 同策略）。
        """
        from backend.app.hasn.model.hasn_agents import HasnAgents
        from backend.app.notification.service.notification_service import notification_service

        # 分身展示名（缺失回落 hasn_id，不造假）
        display_name = (
            await db.execute(select(HasnAgents.display_name).where(HasnAgents.hasn_id == agent_hasn_id))
        ).scalar_one_or_none()
        preview = f'{d.name} · 评分 {d.score}' if d.score is not None else d.name
        # DSGAL：有缺件的场景 → 追加软提示到 preview（缺件为空则不追加，卡片保持简洁）。
        hint = _scene_coverage_hint(scene_coverage) if scene_coverage else None
        if hint:
            preview = f'{preview} · 画廊 {hint}'
        await notification_service.emit(
            db,
            recipient_id=d.owner_hasn_id,
            source={
                'kind': 'agent',
                'id': agent_hasn_id,
                'display_name': display_name or agent_hasn_id,
                'on_behalf_of': d.owner_hasn_id,
            },
            category='agent',
            type='designsystem.ready',
            title=f'设计系统「{d.name}」已完成，点开查看',
            payload={
                'target': {'kind': 'designsystem', 'id': str(d.id)},
                'link': f'/designsystem/{d.id}',
                'preview': preview,
                # DSGAL：结构化覆盖标注也随卡片带出，供 webui 卡片细节渲染（软提示，不阻断）。
                'scene_coverage': scene_coverage or [],
            },
            dedupe_key=f'designsystem.ready:{d.id}',
        )

    async def set_bound_agent(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        design_system_id: int,
        bound_agent_id: str | None,
    ) -> dict[str, Any]:
        """owner 显式改绑/解绑协作分身（AppCollab AC-P3，对称 deck）。

        仅 owner 可改（绑定是 owner 概念，总指向 owner 名下分身）；`None` = 解绑。
        UI 经二次确认调用（`BoundAgentControl`），与 save() 的 bind-only-if-unbound 互补。
        """
        d = await self._get_alive(db, design_system_id)
        if d.is_builtin:
            raise errors.ForbiddenError(msg='内置设计系统不可绑定协作分身')
        if d.owner_hasn_id != owner_hasn_id:
            raise errors.ForbiddenError(msg='只有 owner 可改绑协作分身')
        d.bound_agent_id = bound_agent_id
        d.updated_time = timezone.now()
        await db.commit()
        await db.refresh(d)
        return _ds_dict(d)

    async def set_required_scenes(
        self,
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        design_system_id: int,
        required_scenes: list[str],
    ) -> dict[str, Any]:
        """owner 改「组件画廊要求覆盖的场景」（DSGAL，详情页勾选）。owner-only、非 builtin。

        入参经 _normalize_required_scenes 规整（只留已知场景、去重保序、空回落默认）——
        软提示口径改变，不动任何版本内容（不出新 revision、不改 content_hash）。
        """
        d = await self._get_alive(db, design_system_id)
        if d.is_builtin:
            raise errors.ForbiddenError(msg='内置设计系统不可修改场景要求')
        if d.owner_hasn_id != owner_hasn_id:
            raise errors.ForbiddenError(msg='只有 owner 可修改设计系统的场景要求')
        d.required_scenes = _normalize_required_scenes(required_scenes)
        d.updated_time = timezone.now()
        await db.commit()
        await db.refresh(d)
        return _ds_dict(d)

    async def list_visible(
        self,
        db: AsyncSession,
        *,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """可见域 = builtin ∪ owner ∪ 企业 ∪ 显式共享给我的（P9 resource_share）。"""
        conds = [DesignSystem.is_builtin.is_(True), DesignSystem.owner_hasn_id == viewer_owner_hasn_id]
        if enterprise_id is not None:
            conds.append(DesignSystem.enterprise_id == enterprise_id)
        # 显式共享给我（直接共享给人 / 经我所在企业）→ 并入可见域。
        shared_ids = await resource_share_service.shared_resource_ids_for_human(
            db, resource_type=RESOURCE_TYPE, human_hasn_id=viewer_owner_hasn_id
        )
        shared_int_ids = [int(sid) for sid in shared_ids if sid.isdigit()]
        if shared_int_ids:
            conds.append(DesignSystem.id.in_(shared_int_ids))
        where = [DesignSystem.deleted_time.is_(None), or_(*conds)]
        if category:
            where.append(DesignSystem.category == category)
        total = (await db.execute(select(func.count()).select_from(DesignSystem).where(*where))).scalar_one()
        rows = (
            (
                await db.execute(
                    select(DesignSystem)
                    .where(*where)
                    .order_by(
                        # owner 自己/共享给我的（非 builtin）排在官方内置库之前：内置库 150 套不该把
                        # 「我的设计」挤出首页（默认 limit=50 时尤甚）。webui 两 Tab 各自客户端按
                        # is_builtin 过滤同一份列表（daemon 取 200 全返），此序保证两 Tab 都不丢数据。
                        DesignSystem.is_builtin.asc(), DesignSystem.updated_time.desc(), DesignSystem.id.desc()
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return {'total': int(total), 'items': [_ds_dict(r) for r in rows]}

    async def get(
        self,
        db: AsyncSession,
        *,
        design_system_id: int,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
        with_current_revision: bool = True,
    ) -> dict[str, Any]:
        d = await self._get_alive(db, design_system_id)
        await self._assert_can_read(db, d, viewer_owner_hasn_id, enterprise_id=enterprise_id)
        out = _ds_dict(d)
        if with_current_revision and d.current_revision_id is not None:
            rev = await db.get(Revision, d.current_revision_id)
            out['current_revision'] = _revision_dict(rev) if rev is not None else None
        return out

    async def delete(self, db: AsyncSession, *, design_system_id: int, owner_hasn_id: str) -> None:
        d = await self._get_alive(db, design_system_id)
        if d.owner_hasn_id != owner_hasn_id or d.is_builtin:
            raise errors.ForbiddenError(msg='无权删除该设计系统')
        d.deleted_time = timezone.now()
        await db.commit()

    async def list_revisions(
        self,
        db: AsyncSession,
        *,
        design_system_id: int,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
    ) -> dict[str, Any]:
        d = await self._get_alive(db, design_system_id)
        await self._assert_can_read(db, d, viewer_owner_hasn_id, enterprise_id=enterprise_id)
        rows = (
            (
                await db.execute(
                    select(Revision)
                    .where(Revision.design_system_id == design_system_id)
                    .order_by(Revision.rev_no.desc())
                )
            )
            .scalars()
            .all()
        )
        return {'total': len(rows), 'items': [_revision_dict(r, with_content=False) for r in rows]}

    async def get_revision(
        self,
        db: AsyncSession,
        *,
        revision_id: int,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
    ) -> dict[str, Any]:
        rev = await db.get(Revision, revision_id)
        if rev is None:
            raise errors.NotFoundError(msg='版本不存在')
        d = await self._get_alive(db, rev.design_system_id)
        await self._assert_can_read(db, d, viewer_owner_hasn_id, enterprise_id=enterprise_id)
        return _revision_dict(rev)

    async def compute_owner_revision(self, db: AsyncSession, *, owner_hasn_id: str) -> str:
        """owner 维度同步水位：可见集合（builtin ∪ owner）的 (id, content_hash) 有序聚合的 sha256。

        与 SKAU/PDC content-hash revision 同范式：内容不变 → revision 不变（幂等、可重放）。
        """
        rows = (
            await db.execute(
                select(DesignSystem.id, DesignSystem.content_hash)
                .where(
                    DesignSystem.deleted_time.is_(None),
                    or_(DesignSystem.is_builtin.is_(True), DesignSystem.owner_hasn_id == owner_hasn_id),
                )
                .order_by(DesignSystem.id)
            )
        ).all()
        blob = json.dumps([[r[0], r[1]] for r in rows], separators=(',', ':'))
        return hashlib.sha256(blob.encode('utf-8')).hexdigest()

    # ── 协作分身绑定（对齐 DECKBIND；详见 P9）────────────────────────────
    async def add_collaborator(
        self, db: AsyncSession, *, design_system_id: int, owner_hasn_id: str, agent_hasn_id: str
    ) -> dict[str, Any]:
        d = await self._get_alive(db, design_system_id)
        if d.owner_hasn_id != owner_hasn_id or d.is_builtin:
            raise errors.ForbiddenError(msg='无权为该设计系统绑定协作分身')
        existing = (
            await db.execute(
                select(Collaborator).where(
                    Collaborator.design_system_id == design_system_id,
                    Collaborator.agent_hasn_id == agent_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {'id': existing.id, 'design_system_id': design_system_id, 'agent_hasn_id': agent_hasn_id}
        c = Collaborator(design_system_id=design_system_id, agent_hasn_id=agent_hasn_id, added_by=owner_hasn_id)
        db.add(c)
        await db.commit()
        await db.refresh(c)
        return {'id': c.id, 'design_system_id': design_system_id, 'agent_hasn_id': agent_hasn_id}

    async def list_collaborators(
        self, db: AsyncSession, *, design_system_id: int, viewer_owner_hasn_id: str
    ) -> dict[str, Any]:
        d = await self._get_alive(db, design_system_id)
        await self._assert_can_read(db, d, viewer_owner_hasn_id)
        rows = (
            (await db.execute(select(Collaborator).where(Collaborator.design_system_id == design_system_id)))
            .scalars()
            .all()
        )
        return {'total': len(rows), 'items': [{'id': r.id, 'agent_hasn_id': r.agent_hasn_id} for r in rows]}

    async def remove_collaborator(
        self, db: AsyncSession, *, design_system_id: int, owner_hasn_id: str, agent_hasn_id: str
    ) -> dict[str, Any]:
        """owner 解绑一个协作分身。"""
        d = await self._get_alive(db, design_system_id)
        if d.owner_hasn_id != owner_hasn_id or d.is_builtin:
            raise errors.ForbiddenError(msg='无权管理该设计系统协作分身')
        existing = (
            await db.execute(
                select(Collaborator).where(
                    Collaborator.design_system_id == design_system_id,
                    Collaborator.agent_hasn_id == agent_hasn_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            return {'removed': False}
        await db.delete(existing)
        await db.commit()
        return {'removed': True}

    # ── 分享（人↔人，复用 resource_share；P9）──────────────────────────────
    async def _assert_owner(self, db: AsyncSession, design_system_id: int, owner_hasn_id: str) -> DesignSystem:
        d = await self._get_alive(db, design_system_id)
        if d.owner_hasn_id != owner_hasn_id or d.is_builtin:
            raise errors.ForbiddenError(msg='仅 owner 可管理该设计系统的共享')
        return d

    async def share(
        self,
        db: AsyncSession,
        *,
        design_system_id: int,
        owner_hasn_id: str,
        grantee_type: str,
        grantee_id: str,
        permission: str,
    ) -> dict[str, Any]:
        """共享给他人（viewer/editor）。manager 是 owner 专属，不开放授予。"""
        d = await self._assert_owner(db, design_system_id, owner_hasn_id)
        if permission not in ('viewer', 'editor'):
            raise errors.RequestError(msg='共享权限仅支持 viewer / editor')
        if grantee_type not in ('human', 'agent', 'enterprise'):
            raise errors.RequestError(msg='不支持的授权对象类型')
        row = await resource_share_service.upsert_share(
            db,
            resource_type=RESOURCE_TYPE,
            resource_id=str(d.id),
            owner_hasn_id=owner_hasn_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            permission=permission,
            granted_by=owner_hasn_id,
        )
        # P10：被分享 → 通知人 grantee（同事务 best-effort，通知挂了不连累分享主行为）。
        await self._emit_share_notification(
            db, design_system=d, grantee_type=grantee_type, grantee_id=grantee_id, permission=permission
        )
        await db.commit()
        return row

    @staticmethod
    async def _emit_share_notification(
        db: AsyncSession, *, design_system: DesignSystem, grantee_type: str, grantee_id: str, permission: str
    ) -> None:
        """被分享通知（P10）：仅人 grantee 有通知中心 → 只通知 human；agent/enterprise 跳过（零 fake，不发给无人）。"""
        if grantee_type != 'human':
            return
        try:
            from backend.app.notification.service.notification_service import notification_service

            perm_label = '可编辑' if permission == 'editor' else '可查看'
            await notification_service.emit(
                db,
                recipient_id=grantee_id,
                source={
                    'kind': 'app',
                    'id': RESOURCE_TYPE,
                    'display_name': '设计系统',
                    'on_behalf_of': design_system.owner_hasn_id,
                },
                category='contact',  # doc `通知系统统一设计/01` §2.3-B：他人分享给我=联系人面（非 app）
                type='designsystem.shared',  # type 列 varchar(30)，勿超长
                title=f'有人给你分享了设计系统「{design_system.name}」（{perm_label}）',
                payload={
                    'target': {'kind': RESOURCE_TYPE, 'id': str(design_system.id)},
                    'permission': permission,
                    'deep_link': '/designsystem',
                },
                dedupe_key=f'designsystem.shared:{design_system.id}:{grantee_id}',
            )
        except Exception as e:  # 通知 best-effort
            log.warning('[designsystem] 分享通知发送失败 (非致命): %s', e)

    async def list_shares(
        self, db: AsyncSession, *, design_system_id: int, owner_hasn_id: str
    ) -> dict[str, Any]:
        d = await self._assert_owner(db, design_system_id, owner_hasn_id)
        rows = await resource_share_service.list_shares(db, resource_type=RESOURCE_TYPE, resource_id=str(d.id))
        return {
            'total': len(rows),
            'items': [
                {
                    'id': r['id'],
                    'grantee_type': r['grantee_type'],
                    'grantee_id': r['grantee_id'],
                    'permission': r['permission'],
                    'granted_by': r['granted_by'],
                    'created_time': r['created_time'].isoformat() if r['created_time'] else None,
                }
                for r in rows
            ],
        }

    async def revoke_share(
        self, db: AsyncSession, *, design_system_id: int, owner_hasn_id: str, grantee_type: str, grantee_id: str
    ) -> dict[str, Any]:
        d = await self._assert_owner(db, design_system_id, owner_hasn_id)
        ok = await resource_share_service.revoke_share(
            db, resource_type=RESOURCE_TYPE, resource_id=str(d.id), grantee_type=grantee_type, grantee_id=grantee_id
        )
        await db.commit()
        return {'revoked': ok}

    # ── D6：采用 / 回滚版本（owner 裁决，含协作待确认版）─────────────────────
    async def set_current_revision(
        self, db: AsyncSession, *, design_system_id: int, revision_id: int, owner_hasn_id: str
    ) -> dict[str, Any]:
        """把某一版设为当前版（采用协作待确认版 或 回滚到历史版）。owner-only。

        回填根字段：content_hash 从该版内容重算（驱动 daemon 增量重拉）；score/grade/recommend
        从该版评分报告 summary 取（确定性，非 LLM）。
        """
        d = await self._assert_owner(db, design_system_id, owner_hasn_id)
        rev = await db.get(Revision, revision_id)
        if rev is None or rev.design_system_id != design_system_id:
            raise errors.NotFoundError(msg='版本不存在')
        content = {k: getattr(rev, k) for k in _REVISION_CONTENT}
        d.current_revision_id = rev.id
        d.content_hash = _content_hash(content)
        # 采用/回滚版本同步刷新列表卡预览色板（denorm 自该版 tokens.css）。
        d.preview_swatches = _extract_preview_swatches(rev.tokens_css)
        summary = (rev.token_contract_report_json or {}).get('summary') if isinstance(rev.token_contract_report_json, dict) else None
        if isinstance(summary, dict):
            d.score = summary.get('score')
            d.grade = summary.get('grade')
            d.recommend_rebuild = bool(summary.get('recommendRebuild', False))
        d.updated_time = timezone.now()
        await db.commit()
        await db.refresh(d)
        out = _ds_dict(d)
        out['current_revision'] = _revision_dict(rev)
        return out


design_system_service: DesignSystemService = DesignSystemService()
