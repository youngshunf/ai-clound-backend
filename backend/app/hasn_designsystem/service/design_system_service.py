"""设计系统生成应用（app_id=designsystem）云端业务服务。

云端是**权威存储**：存 tokens.css + 派生产物 + 评分报告，并维护版本与同步水位。

⚠️ 「云端不重算」这句**已随 TOOLMIG 作废**：确定性契约引擎（compile/derive/validate/extract）已
Python 移植进 :mod:`backend.app.hasn_designsystem.core`，云端自己就能算。整包 ``save`` 仍沿用旧口径
（分身给什么存什么，保持兼容），而**分片写入路径（DSPUT）由云端现算全部派生物**——见
:meth:`DesignSystemService.put_content`。派生物本就是纯函数的输出，让分身把算好的结果再序列化一遍
穿过 tool.call，是这条链路上最没必要的那部分体积。

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
from uuid import UUID

from sqlalchemy import func, or_, select

from backend.app.hasn.service.authz import Subject  # G6：收编来源，模块级再导出（既有调用点不变）
from backend.app.hasn.service.resource_share_service import rank, resource_share_service
from backend.app.hasn_designsystem.core.components import extract_components
from backend.app.hasn_designsystem.core.contract import validate as validate_token_contract
from backend.app.hasn_designsystem.core.derive import derive as derive_from_tokens
from backend.app.hasn_designsystem.core.gallery_compose import remove_scene, upsert_scene
from backend.app.hasn_designsystem.core.gallery_health import assess_gallery_health
from backend.app.hasn_designsystem.core.gallery_projection import (
    slice_gallery_scene,
    summarize_gallery,
)
from backend.app.hasn_designsystem.core.scenes import (
    DEFAULT_REQUIRED_SCENES,
    SCENE_STANDARDS,
    detect_scenes,
    is_known_scene,
)
from backend.app.hasn_designsystem.model import Collaborator, DesignSystem, Revision
from backend.app.hasn_designsystem.service.scene_guidance import build_scene_report
from backend.app.hasn_project.service.project_app_service import project_service
from backend.common.exception import errors
from backend.utils.timezone import timezone

log = logging.getLogger(__name__)

# 共享产物类型（统一 resource_share 表里的 designsystem 命名空间，与 deck/doc/knowledge 并列）。
RESOURCE_TYPE = 'designsystem'

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.app.hasn_im.ports.im_gateway import ImGateway

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


# ── DSPUT·分片写入：由服务端现算的派生内容字段 ────────────────────────────────────
# 这四项都是 tokens.css / components.html 经确定性纯函数得到的**函数值**，不是独立信息。整包 save
# 时代它们要由分身算好再回传，于是几十 KB 的 manifest 与报告白白穿过一次 tool.call 的双重 JSON
# 序列化——而这正是实测 41% 调用「生成不出合法 JSON」的体积来源。分片路径一律云端现算。
# 真源：只有这两项是分身真正创作的内容，其余内容字段都是它们的函数。
_CONTENT_SOURCE_FIELDS = ('tokens_css', 'components_html')

_SERVER_DERIVED_FIELDS = (
    'design_tokens_json',
    'tailwind_css',
    'token_contract_report_json',
    'components_manifest_json',
)


def _recompute_derived(content: dict[str, Any], *, brand_id: str, generated_at: str) -> dict[str, Any]:
    """按 ``tokens_css`` + ``components_html`` 现算全部派生物，返回**新的** content（不改入参）。

    真源缺失时对应派生物原样保留（不清空、不臆造）——建壳后只写了 design.md 的中间态是合法的，
    此时还没有 tokens.css 可算。
    """
    out = dict(content)
    tokens_css = out.get('tokens_css')
    html = out.get('components_html')
    tokens_css = tokens_css if isinstance(tokens_css, str) and tokens_css.strip() else None
    html = html if isinstance(html, str) and html.strip() else None

    if tokens_css is not None:
        out['token_contract_report_json'] = validate_token_contract(tokens_css, generated_at, html)
        derived = derive_from_tokens(tokens_css, generated_at)
        # render_design_tokens_json 返回的是渲染好的 JSON **文本**，而 DB 列是 JSONB → 存 dict。
        out['design_tokens_json'] = json.loads(derived['design_tokens_json'])
        out['tailwind_css'] = derived['tailwind_v4_css']
    if html is not None:
        out['components_manifest_json'] = extract_components(brand_id, html, tokens_css)
    return out


def _score_from_report(report: Any) -> tuple[int | None, str | None, bool]:
    """从契约报告里取 ``(score, grade, recommend_rebuild)``；报告形状不对时一律返回空，不猜。"""
    summary = report.get('summary') if isinstance(report, dict) else None
    if not isinstance(summary, dict):
        return None, None, False
    score = summary.get('score')
    grade = summary.get('grade')
    return (
        score if isinstance(score, int) and not isinstance(score, bool) else None,
        grade if isinstance(grade, str) and grade else None,
        bool(summary.get('recommendRebuild')),
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


def _authoritative_scenes(components_html: Any) -> list[dict[str, Any]]:
    """零信任：场景覆盖一律据实际 ``components_html`` 重算（与 ``check_scenes`` 同源 ``detect_scenes``）。

    绝不信分身自带 manifest 里的 ``scenes[]``——它是分身产出时的一次快照，可能与最终画廊 HTML 漂移
    （分身把画廊标记补齐了却没重跑抽取器、或手写了 manifest），于是详情页「明明配齐却显示 0/N 缺」、
    而 ``check_scenes`` 却通过（后者一直读实时 HTML）。对齐 ``gallery_health``「不信自带 manifest，
    零信任重算」。HTML 缺失/非法 → 空列表。
    """
    if not isinstance(components_html, str) or not components_html:
        return []
    return detect_scenes(components_html)


def _manifest_with_scenes(manifest: Any, components_html: Any) -> Any:
    """序列化边界：把 manifest 的 ``scenes[]`` 换成据 ``components_html`` 实测的权威覆盖报告。

    manifest 是 dict → 浅拷贝后覆盖 ``scenes``（绝不原地改 ORM 的 JSONB）；manifest 缺失/畸形但 HTML
    检出场景 → 合成最小 ``{'scenes': [...]}`` 让详情页覆盖分区能算；两者皆空 → 原样返回（None/原值）。
    这样存量行无需重存即修好、且详情页覆盖分区与 ``check_scenes`` 逐场景恒一致（同一 detect_scenes）。
    """
    scenes = _authoritative_scenes(components_html)
    if isinstance(manifest, dict):
        return {**manifest, 'scenes': scenes}
    if scenes:
        return {'scenes': scenes}
    return manifest


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
        'platform_project_id': str(d.platform_project_id) if d.platform_project_id is not None else None,
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
        out.update({
            'tokens_css': r.tokens_css,
            'design_tokens_json': r.design_tokens_json,
            'tailwind_css': r.tailwind_css,
            'design_md': r.design_md,
            'components_html': r.components_html,
            # DSGAL 修：场景覆盖据实际 components_html 重算后注入 manifest（零信任），根治「组件画廊已配齐
            # 但详情页场景覆盖显示 0/N 缺」——存量行无需重存即修好，且与 check_scenes 逐场景恒一致。
            'components_manifest_json': _manifest_with_scenes(r.components_manifest_json, r.components_html),
            'token_contract_report_json': r.token_contract_report_json,
        })
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
        platform_project_id: str | UUID | None = None,
        im_gateway: ImGateway | None = None,
    ) -> dict[str, Any]:
        """创建或更新一套设计系统：落一版 revision + 回填 current/content_hash/评分。

        `content` 含 tokens.css + 派生 + 创意（见 _REVISION_CONTENT）。每次 save 都出一版（可回滚）。
        `required_scenes`：owner 派发时要求覆盖的组件画廊场景（None=不改，新建时回落默认 [brand_website]）。
        `platform_project_id` 仅在实际插入新行时挂靠；同 slug 幂等命中和显式更新都属于存量更新，
        改挂必须走项目 link/unlink。
        """
        owner = subject.owner_hasn_id

        # 组件画廊可渲染性硬闸（山茶茶间事故根治·零 fake 不把坏画廊静默入库）：正文用了 CSS 类名
        # 却零组件样式规则、又没内联样式兜底 → 画廊会退化成裸语义 HTML。此时拒绝落库，把可照做的
        # 整改说明诚实回给分身（二选一：全内联 var(--token) / 或 <style> 写全类规则），让它自行修好再存。
        health = assess_gallery_health(content.get('components_html') if isinstance(content, dict) else None)
        if not health.healthy:
            raise errors.RequestError(msg=health.reason)

        hashed = _content_hash(content)
        now = timezone.now()

        created_new_row = False
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
                created_new_row = True
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

        # 容器级自动挂靠只发生在真正插入新行时；slug 幂等命中属于存量更新，不得借当前
        # 项目上下文隐式改变挂靠。存量改挂/摘除统一走 hasn.project.link/unlink。
        if created_new_row and platform_project_id is not None:
            await project_service.assert_owned(db, owner=owner, pk=platform_project_id)
            d.platform_project_id = UUID(str(platform_project_id))

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

        # 分身写满必填字段时，在设计系统状态事务内登记完成卡 outbox 命令。状态提交后即使
        # API/worker 崩溃，relay 仍能恢复投递；业务事务失败则命令一并回滚。
        completion_card = await self._emit_completion_card_if_needed(
            db,
            d=d,
            subject=subject,
            content=content,
            now=now,
            im_gateway=im_gateway,
            advance_current=advance_current,
        )

        await db.commit()
        await db.refresh(d)

        out = _ds_dict(d)
        out['revision'] = _revision_dict(rev, with_content=False)
        out['pending'] = not advance_current  # True=协作待确认版（未落当前态）
        # 完成信号仅用于工具返回清理与诊断；投递命令已在 save 事务中持久化。
        out['completion_card'] = completion_card
        return out

    # ── DSPUT·分片写入（create → put_* → finalize）───────────────────────────────────
    # 整包 save 的问题不在业务逻辑，在**入参体积**：改一个场景也要把全部场景 HTML 一起重发，
    # 经 tool.call 套一层 JSON 后分身要一次吐出 2 万-4.5 万字符的双重转义串。实测 104 次调用
    # 66% 失败，其中 41% 卡在「生成不出合法 JSON」。分片路径让每次入参只带**本次真正要改的那一块**，
    # 未提供的字段由服务端从当前版继承、派生物由服务端现算——两者都不再经过分身的手。

    async def create_shell(
        self,
        db: AsyncSession,
        *,
        subject: Subject,
        slug: str,
        name: str,
        category: str | None = None,
        source_kind: str = 'generated',
        required_scenes: list[str] | None = None,
        platform_project_id: str | UUID | None = None,
        enterprise_id: int | None = None,
    ) -> dict[str, Any]:
        """建一套空设计系统的壳，只要 ``slug`` + ``name``，**不落任何 revision**。

        同 owner 撞 slug 视为命中存量（与 save 的幂等口径一致），返回既有那套——分身重试建壳不会
        造出第二套，也不会覆盖已有内容。

        不落空 revision 是有意的：revision 表示「一版内容」，空壳没有内容。这样
        :func:`_content_complete` 天然为 False，详情页四区块如实显示为空，不会出现「有版本号但全空」。
        """
        owner = subject.owner_hasn_id
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
            out = _ds_dict(existing)
            out['created'] = False  # 幂等命中：如实告诉调用方这次没有新建
            return out

        d = DesignSystem(
            owner_hasn_id=owner,
            name=name,
            slug=slug,
            category=category,
            source_kind=source_kind,
            enterprise_id=enterprise_id,
            content_hash='',
        )
        if required_scenes is not None:
            d.required_scenes = _normalize_required_scenes(required_scenes)
        db.add(d)
        await db.flush()
        if platform_project_id is not None:
            await project_service.assert_owned(db, owner=owner, pk=platform_project_id)
            d.platform_project_id = UUID(str(platform_project_id))
        # AppCollab：创建即绑定生成它的分身（与 save 同 bind-only-if-unbound 口径）。
        if d.bound_agent_id is None and subject.kind == 'agent':
            d.bound_agent_id = subject.hasn_id
        await db.commit()
        await db.refresh(d)
        out = _ds_dict(d)
        out['created'] = True
        return out

    async def put_content(
        self,
        db: AsyncSession,
        *,
        subject: Subject,
        design_system_id: int,
        patch: dict[str, Any],
        note: str | None = None,
        required_scenes: list[str] | None = None,
        skip_if_unchanged: bool = False,
        im_gateway: ImGateway | None = None,
    ) -> dict[str, Any]:
        """部分内容写入：**未提供的字段从当前版继承**，派生物由服务端现算，落一版新 revision。

        ``patch`` 只放本次真正要改的键（``tokens_css`` / ``design_md`` / ``components_html``）。
        这正是分片相对整包替换的核心差别——整包 save 里「没传」等于「清空」（实测漏传
        ``components_html`` 让画廊从 13/13 变 0/13，而 save 照样返回 200、score 仍是 100）；
        这里「没传」等于「不动」。

        权限：先按读权限断言（无权者不该借写接口读到内容），写权限由 :meth:`save` 兜底判定。
        """
        d = await self._get_alive(db, design_system_id)
        await self._assert_can_read(db, d, subject.owner_hasn_id)

        current: dict[str, Any] = {}
        if d.current_revision_id is not None:
            rev = await db.get(Revision, d.current_revision_id)
            if rev is not None:
                current = {k: getattr(rev, k) for k in _REVISION_CONTENT}

        unknown = [k for k in patch if k not in _REVISION_CONTENT]
        if unknown:
            raise errors.RequestError(msg=f'不认识的内容字段: {unknown}（可写：{list(_REVISION_CONTENT)}）')
        merged = {**current, **patch}
        # 只有真源（tokens.css / components.html）动了才重算派生物。派生物是真源的函数，真源没变
        # 结果就没变——而重算会把新的 generatedAt 时间戳写进报告，让 content_hash 每次都不同，
        # finalize 这种「什么都没改」的调用就会永远落一版冗余 revision（且永远判 unchanged=False）。
        if any(key in patch for key in _CONTENT_SOURCE_FIELDS):
            merged = _recompute_derived(merged, brand_id=d.slug, generated_at=timezone.now().isoformat())
        score, grade, recommend_rebuild = _score_from_report(merged.get('token_contract_report_json'))

        if skip_if_unchanged and d.current_revision_id is not None and _content_hash(merged) == d.content_hash:
            # 内容与当前版逐字节相同（finalize 的常态）→ 不落冗余版本，但**完成判定与发卡照做**：
            # 首次「写全」很可能正发生在最后那次 put，此刻才是该发卡的时刻。
            now = timezone.now()
            completion_card = await self._emit_completion_card_if_needed(
                db, d=d, subject=subject, content=merged, now=now, im_gateway=im_gateway
            )
            if required_scenes is not None:
                d.required_scenes = _normalize_required_scenes(required_scenes)
            d.updated_time = now
            await db.commit()
            await db.refresh(d)
            out = _ds_dict(d)
            out['revision'] = None
            out['unchanged'] = True
            out['pending'] = False
            out['completion_card'] = completion_card
            out['content_complete'] = _content_complete(merged)
            return out

        out = await self.save(
            db,
            subject=subject,
            design_system_id=design_system_id,
            # slug/name 取库里现值：更新存量时它们不该由调用方再给一遍——slug 在 save 的白名单里
            # 本就改不动（传了静默忽略），name 让分身每次原样回传只会带来「传错就静默改名」的风险。
            slug=d.slug,
            name=d.name,
            content=merged,
            category=None,
            source_kind=d.source_kind,
            score=score,
            grade=grade,
            recommend_rebuild=recommend_rebuild,
            note=note,
            required_scenes=required_scenes,
            im_gateway=im_gateway,
        )
        out['unchanged'] = False
        # 完整度是「能不能说做完了」的唯一判据，显式回出去——别让分身自己数五项必填。
        out['content_complete'] = _content_complete(merged)
        return out

    async def put_gallery_scene(
        self,
        db: AsyncSession,
        *,
        subject: Subject,
        design_system_id: int,
        scene: str,
        html: str,
        note: str | None = None,
        im_gateway: ImGateway | None = None,
    ) -> dict[str, Any]:
        """按**交付物场景**写组件画廊：该场景整体替换，**其余场景原地不动**。

        单次入参只带一个场景的 markup（几 KB），而不是整包画廊（几十 KB）。写入前读回当前整包在
        服务端合并——大数据始终留在服务端，不往返分身。
        """
        d = await self._get_alive(db, design_system_id)
        await self._assert_can_read(db, d, subject.owner_hasn_id)
        current_html = None
        if d.current_revision_id is not None:
            rev = await db.get(Revision, d.current_revision_id)
            current_html = rev.components_html if rev is not None else None
        try:
            merged_html = upsert_scene(current_html, scene, html)
        except ValueError as exc:  # 未知场景 / 空 markup / 场景与 markup 不符 → 如实回给分身
            raise errors.RequestError(msg=str(exc)) from exc

        out = await self.put_content(
            db,
            subject=subject,
            design_system_id=design_system_id,
            patch={'components_html': merged_html},
            note=note or f'写入场景 {scene}',
            im_gateway=im_gateway,
        )
        out['scene'] = scene.strip().lower()
        return out

    async def remove_gallery_scene(
        self,
        db: AsyncSession,
        *,
        subject: Subject,
        design_system_id: int,
        scene: str,
        im_gateway: ImGateway | None = None,
    ) -> dict[str, Any]:
        """从画廊里删掉一个场景。没删到时如实回 ``removed=False``，不假装成功。"""
        d = await self._get_alive(db, design_system_id)
        await self._assert_can_read(db, d, subject.owner_hasn_id)
        current_html = None
        if d.current_revision_id is not None:
            rev = await db.get(Revision, d.current_revision_id)
            current_html = rev.components_html if rev is not None else None
        merged_html, removed = remove_scene(current_html, scene)
        if not removed:
            out = _ds_dict(d)
            out['removed'] = False
            out['scene'] = scene.strip().lower()
            return out
        out = await self.put_content(
            db,
            subject=subject,
            design_system_id=design_system_id,
            patch={'components_html': merged_html},
            note=f'移除场景 {scene}',
            im_gateway=im_gateway,
        )
        out['removed'] = True
        out['scene'] = scene.strip().lower()
        return out

    async def finalize(
        self,
        db: AsyncSession,
        *,
        subject: Subject,
        design_system_id: int,
        required_scenes: list[str] | None = None,
        im_gateway: ImGateway | None = None,
    ) -> dict[str, Any]:
        """定稿：重算派生物 → 完整度判定（够格则发完成卡）→ 场景覆盖自查报告一并回给分身。

        **不需要分身再传任何内容**——要定的稿已经在库里了。把「重算 + 发卡 + 自查」收进一次调用，
        是为了消掉「分身 save 完自己判断算不算完成、又忘了调 check_scenes」这条一直靠纪律维持的
        环节：技能里反复强调「save 后汇报前必须 check_scenes」，而纪律是会漏的，闸门不会。
        """
        out = await self.put_content(
            db,
            subject=subject,
            design_system_id=design_system_id,
            patch={},
            note='定稿',
            required_scenes=required_scenes,
            skip_if_unchanged=True,
            im_gateway=im_gateway,
        )
        out['scene_report'] = await self.scene_coverage_report(
            db,
            design_system_id=design_system_id,
            viewer_owner_hasn_id=subject.owner_hasn_id,
        )
        # ⚠️ complete 取「五项必填是否真的写全」，**不是**「发过完成卡没有」：completed_notified_at
        # 只是发卡幂等水位，owner 本人操作永远不发卡（没有分身可署名），拿它当完整度会让
        # owner 路径恒为 False。两个概念在这里必须分开。
        out['complete'] = bool(out.get('content_complete'))
        return out

    async def _emit_completion_card_if_needed(
        self,
        db: AsyncSession,
        *,
        d: DesignSystem,
        subject: Subject,
        content: dict[str, Any],
        now: Any,
        im_gateway: ImGateway | None,
        advance_current: bool = True,
    ) -> dict[str, Any] | None:
        """内容首次写全 → 在当前事务内登记「设计系统已完成·查看」卡的 outbox 命令。

        从 :meth:`save` 里原样抽出，供整包 save 与分片 finalize **共用同一份判定**——完成判定
        （五项必填全非空）与幂等水位（``completed_notified_at``）只能有一处口径，两条写入路径
        各写一份迟早分叉：一条发卡、另一条不发，而两边看起来都「按设计做了」。

        返回非 None 表示本次登记了投递命令（调用方据此在 post-commit 回填水位）。
        """
        should_notify = (
            advance_current
            and subject.kind == 'agent'
            and d.completed_notified_at is None
            and _content_complete(content)
        )
        if not should_notify:
            return None

        # DSGAL：交叉 owner 要求与实际画廊场景，把覆盖提示压进完成卡摘要。
        preview = f'{d.name} · 评分 {d.score}' if d.score is not None else d.name
        hint = _scene_coverage_hint(
            _scene_coverage_annotation(
                _normalize_required_scenes(d.required_scenes),
                _authoritative_scenes(content.get('components_html')),
            )
        )
        if hint:
            preview = f'{preview} · 画廊 {hint}'
        from backend.app.hasn.service.hasn_sessions_service import (
            emit_designsystem_completion_card,
        )

        delivery = await emit_designsystem_completion_card(
            db,
            # 与抽出前逐字保持 subject.owner_hasn_id：发卡路径要求 advance_current，
            # 而 advance_current 已蕴含 d.owner_hasn_id == subject.owner_hasn_id（协作方改别人的
            # 设计系统只落待确认版、不发卡），两者在这条路径上恒等——但不借等价性改写它。
            owner_id=subject.owner_hasn_id,
            agent_id=subject.hasn_id,
            design_system_id=str(d.id),
            title=d.name,
            summary=preview,
            im_gateway=im_gateway,
        )
        if not delivery.get('command_id'):
            return None
        d.completed_notified_at = now
        return {
            'design_system_id': str(d.id),
            'title': d.name,
            'summary': preview,
            'delivery_command_id': delivery['command_id'],
            'delivery_state': delivery['status'],
        }

    async def mark_completion_notified(self, db: AsyncSession, design_system_id: int) -> None:
        """完成卡投递成功后回填 completed_notified_at（工具 post-commit 调，独立事务，自提交）。

        与 save() 的 `completed_notified_at is None` 门配套：只有投递成功才标已发，故首投失败会留空、
        下次完整 save 自愈补发。已标过再调是幂等 no-op（不覆盖既有时间戳）。行不存在则静默跳过。
        """
        d = await db.get(DesignSystem, design_system_id)
        if d is None or d.completed_notified_at is not None:
            return
        d.completed_notified_at = timezone.now()
        await db.commit()

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
        platform_project_id: str | UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """可见域 = builtin ∪ owner ∪ 企业 ∪ 显式共享；项目仅在显式传参时过滤。"""
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
        if platform_project_id is not None:
            await project_service.assert_owned(db, owner=viewer_owner_hasn_id, pk=platform_project_id)
            where.append(DesignSystem.platform_project_id == UUID(str(platform_project_id)))
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
                        DesignSystem.is_builtin.asc(),
                        DesignSystem.updated_time.desc(),
                        DesignSystem.id.desc(),
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

    async def scene_coverage_report(
        self,
        db: AsyncSession,
        *,
        design_system_id: int,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
        components_html_override: str | None = None,
        required_scenes_override: list[str] | None = None,
    ) -> dict[str, Any]:
        """DSGAL 自查（`hasn.designsystem.check_scenes` 的 service 入口）：交叉 required_scenes × 当前
        components.html 实检覆盖 → 逐场景「已配齐 X/Y · 缺哪几件 + 怎么补」的可执行报告。

        - 读设计系统 + 判权（与 get 同 ACL），取**当前版本 components.html** 现读现检测（不依赖可能陈旧的
          manifest.scenes[]，故 owner 编辑了 HTML 但没重抽取 manifest 也能诚实反映）。
        - ``components_html_override``：非空则改用它检测（分身**存前 dry-run** 自己的草稿）。
        - ``required_scenes_override``：非空则改用它作为要求（否则用库里存的 required_scenes）。
        - 返回 :func:`scene_guidance.build_scene_report` 的报告（附 design_system_id/name）。
        """
        d = await self._get_alive(db, design_system_id)
        await self._assert_can_read(db, d, viewer_owner_hasn_id, enterprise_id=enterprise_id)

        html = components_html_override
        if not html and d.current_revision_id is not None:
            rev = await db.get(Revision, d.current_revision_id)
            html = rev.components_html if rev is not None else None
        required = required_scenes_override if required_scenes_override is not None else d.required_scenes

        return build_scene_report(required, html, design_system_id=d.id, name=d.name)

    async def get_gallery(
        self,
        db: AsyncSession,
        *,
        design_system_id: int,
        viewer_owner_hasn_id: str,
        enterprise_id: int | None = None,
        scene: str | None = None,
    ) -> dict[str, Any]:
        """取一套设计系统的**组件画廊 HTML**（DSGET·分身按需取数入口，与 get/check_scenes 同 ACL）。

        默认 ``get`` 已不回灌整包画廊（省 token）；分身要参考/编辑组件时经本入口按需取：
        - ``scene`` 缺省 → 返回当前版本**整包** ``components_html``（+ manifest）；
        - ``scene`` 给定且该场景有 ``<section data-ds-scene>`` 容器 → **只切该场景**（带全量 ``<style>``
          保证自包含可渲染），``slice_applied=True``；切不动（无容器/未知场景）→ 诚实回退整包。

        ``available_scenes`` 恒据**原始整包**折出（切片不影响它），让分身知道还有哪些场景可再取。
        """
        d = await self._get_alive(db, design_system_id)
        await self._assert_can_read(db, d, viewer_owner_hasn_id, enterprise_id=enterprise_id)

        original_html: str | None = None
        manifest: Any = None
        if d.current_revision_id is not None:
            rev = await db.get(Revision, d.current_revision_id)
            if rev is not None:
                original_html = rev.components_html
                manifest = rev.components_manifest_json

        summary = summarize_gallery(original_html)
        scene_id = scene.strip().lower() if isinstance(scene, str) and scene.strip() else None

        out_html = original_html
        slice_applied = False
        if scene_id and original_html:
            out_html, slice_applied = slice_gallery_scene(original_html, scene_id)

        result: dict[str, Any] = {
            'design_system_id': d.id,
            'name': d.name,
            'scene': scene_id,
            'slice_applied': slice_applied,
            'components_html': out_html,
            'available_scenes': [s['id'] for s in summary['scenes']],
        }
        # manifest 是整包统计，只在**整包取**（未切片）时带上；按场景切片时省略（分身只要那一场景的 markup）。
        # 同 get 详情：manifest.scenes[] 据实际整包 HTML 重算（零信任），保证分身取回的覆盖报告与实况一致。
        if not slice_applied:
            result['components_manifest_json'] = _manifest_with_scenes(manifest, original_html)
        return result

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

    async def list_shares(self, db: AsyncSession, *, design_system_id: int, owner_hasn_id: str) -> dict[str, Any]:
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
        summary = (
            (rev.token_contract_report_json or {}).get('summary')
            if isinstance(rev.token_contract_report_json, dict)
            else None
        )
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
