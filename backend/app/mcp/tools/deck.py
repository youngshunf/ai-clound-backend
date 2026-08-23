"""平台工具 · deck 域（演示文稿，模块 17）。

把分身的 deck 工具从 hasn-node 本地 hasn-mcp 迁到**云端 platform MCP 工具**（TOOLMIG2-P3，
福仔 2026-06-27 选 B：完整迁 deck，云端分身可完整创作演示文稿）：分身经
`/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `deck_service`（in-process，
**不再**经 daemon → cloud agent REST relay）。owner/分身身份由 Agent JWT/MCP Key 解析出的
`AgentContext` 强制（`Subject.agent(agent, owner)`），身份绝不入请求体。

与 task/workflow（薄云端代理）不同，deck 创作侧原依赖本地 daemon DeckBroker（骨架校验 + 按
position upsert 编排 + 整图重排）——这些**不依赖本机资源**、是纯云端可执行的编排，故按 B 方案
在此忠实复刻：
- 骨架校验 = `backend/app/hasn_deck/service/page_skeleton.py`（移植自 daemon Rust 校验器，逐字对齐）；
- 按 position 序数 upsert（已有该位 → update_page，否则 create_page）= 复刻 daemon `write_pages`；
- 整图重排 = `deck_service.reorder_pages`（云端新增方法，两段式负位，对齐 daemon broker.reorder_pages）；
- outline 存储形状 = `{'items': pages}`（与 daemon→云端 sync 一致，见 daemon `deck/sync.rs`）。

工具名 + scope 与本地 hasn-mcp `crates/hasn-mcp/src/deck.rs` 1:1（读类 4 无 scope；写类 8 = `deck:manage`）。
deck_id / page_id 工具入参是 string，service 收 int（统一 `int(...)`）。
写类经 `async_db_session.begin()` 自动提交（service 只 flush）。三态闸门由 `server.call_tool` 统一判定。
"""

from __future__ import annotations

import logging

from collections.abc import Awaitable, Callable
from typing import Any

from backend.app.hasn.schema.resource_descriptor import ArtifactRegistration
from backend.app.hasn_deck.service import resource_adapter as _deck_resource_adapter  # noqa: F401  # G6 使用点注册兜底
from backend.app.hasn_deck.service.deck_service import Subject, deck_service
from backend.app.hasn_deck.service.page_skeleton import validate_page_skeleton
from backend.app.mcp.artifact_registration import merge_resource_uri, register_app_resource_artifact
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.context import get_current_project_id
from backend.app.mcp.tools.base import BaseTool
from backend.common.exception import errors
from backend.database.db import async_db_session

logger = logging.getLogger(__name__)

NAMESPACE = 'hasn.deck'
SCOPE_MANAGE = 'deck:manage'

Handler = Callable[[Any, AgentContext, dict[str, Any]], Awaitable[Any]]


def _owner_hasn_id(ctx: AgentContext) -> str:
    """从已认证 Agent 上下文取得主人 HASN ID；缺失时显式拒绝。"""
    owner_hasn_id = ctx.owner_hasn_id
    if not owner_hasn_id:
        raise errors.TokenError(msg='AgentContext 缺少 owner_hasn_id')
    return owner_hasn_id


async def _bump_decks_sync(owner_hasn_id: str | None) -> None:
    """分身 deck 写点提交后 → WSPUSH ``hasn.sync.invalidate(decks)`` 给主人在线节点（best-effort）。

    LFRT 刀4（实施/90 §2.1）：治「分身把 PPT 做完，主人打开列表/详情还是空白」——本模块
    是分身 deck 写的唯一云端面（TOOLMIG2-P3 后本地 hasn-mcp deck 工具已摘除），bump 让主人
    daemon 秒级 nudge webui 重拉 deck 视图。事务提交后另开只读会话算指纹（读到已提交数据）。
    push 失败不抛——离线设备靠打开页面时 read-through 兜底追平，写点绝不因推送失败而失败。
    """
    if not owner_hasn_id:
        return
    try:
        from backend.app.hasn.service import sync_invalidate_service as siv

        async with async_db_session() as db:
            await siv.bump_owner(siv.KIND_DECKS, db, owner_hasn_id)
    except Exception as e:
        logger.warning('[deck] agent 写点 sync invalidate 推送失败 (非致命): %s', e)


async def _register_deck_artifact(
    db: Any, ctx: AgentContext, deck_id: int, title: str | None = None
) -> ArtifactRegistration | None:
    """register-on-write（doc31/32 RC-P8 泛化）：分身**每个 deck 写点**都把该 deck 登记进
    `hasn_artifacts`——不再等 finalize，create→逐页写→改稿全程可见。

    这治「分身建/改了 deck，会话资源栏 / 分身产物 tab 却看不到」：
    - `session_id = ctx.session_id`（工作会话派发时由 Hermes/daemon 戳 `_hasn_session_id`、
      server.call_tool 剥离落 ctx；主会话直调则为 None，产物仍凭 resource_uri 进产物 tab）；
    - `server_id = str(deck_id)`（云端权威 deck id，Core-08 第二原则：本地 id 永不上 URI）；
    - 幂等 upsert（键 `(agent, deck:{deck_id}, hasn://deck/{deck_id})`）——反复写不重复登记、
      会话归属只进不退，与 finalize 完成卡投影同键，两路径重复触发也只一条 active 行。

    doc36 U1：改走**公共接缝** `register_app_resource_artifact`（此前绕过接缝直调 service +
    自己取 descriptor，正是 `ROLLOUT` 守卫覆盖不到 deck 的原因）。返回 `ArtifactRegistration`
    供写工具把 `uri` 放进返回体。best-effort 语义由接缝保证：登记失败**绝不**拖垮 deck 写本身。
    """
    resolved_title = (title or '').strip() or None
    if resolved_title is None:
        # 页写点常不带标题——补取 deck 权威标题（同事务只读，无额外提交）。
        try:
            deck = await deck_service.get_deck(db, subject=_subject(ctx), deck_id=deck_id)
            resolved_title = str(deck.get('title') or '').strip() or None
        except Exception:
            resolved_title = None
    return await register_app_resource_artifact(
        db,
        app_id='deck',
        resource_kind='deck.presentation',
        server_id=str(deck_id),
        # 会话轴分流（设计 02 §4.3）：不显式传 session_id——`ctx.session_id` 是运行时/逻辑会话
        # 语义，直传会污染工作会话列；缺省走 ContextVar 两级权威。
        agent_hasn_id=ctx.agent_hasn_id,
        owner_hasn_id=_owner_hasn_id(ctx),
        title=resolved_title or '演示文稿',
        source_tool=f'{NAMESPACE}.write',
    )


def _subject(ctx: AgentContext) -> Subject:
    return Subject.agent(ctx.agent_hasn_id, _owner_hasn_id(ctx))


# ── 返回体投影（省上下文）─────────────────────────────────────────────────────────
# 这些工具的调用方是**分身**，返回体直接进它的上下文并按 token 计费。service 层的
# `_page_dict` / `_deck_dict` / `_style_profile_dict` 是给 REST 面（webui 要拿全量渲染）用的，
# 原样透传给分身会把分身**刚刚自己传上来的内容**再回显一遍：一页 16:9 创意 HTML 常有几 KB 到
# 几十 KB，改一个标题也要付整页的钱，批量写更是一次回显整份 PPT。写点一律只回「定位 + 校验」
# 所需的字段，正文不回显；要读回正文走读工具并显式开 `include_html`。


def _page_brief(page: dict[str, Any], *, include_html: bool = False, include_notes: bool = False) -> dict[str, Any]:
    """页投影：默认只回定位与状态字段，`html` 正文不回显。

    `html_length` 是留给分身的**廉价校验位**——分身写完想确认内容没被截断时比对字符数即可，
    不必把整段 HTML 拉回上下文再逐字比。`notes`（演讲备注）通常只有几行，读工具会带上，
    因为分身批量改写前需要先取回它以免丢失。
    """
    out: dict[str, Any] = {
        'id': page['id'],
        'position': page['position'],
        'title': page['title'],
        'status': page['status'],
        'rev': page['rev'],
        'html_length': len(str(page.get('html') or '')),
    }
    if include_notes:
        out['notes'] = page.get('notes')
    if include_html:
        out['html'] = page.get('html')
    return out


def _deck_brief(deck: dict[str, Any]) -> dict[str, Any]:
    """deck 投影：去掉 `outline` / `design_contract` 两个大字段。

    写点（create / outline.set）回显它们等于把分身刚传的大纲原样退回；分身要读大纲用
    `hasn.deck.get`，那里保留完整 deck。
    """
    keep = (
        'id',
        'title',
        'topic',
        'status',
        'language',
        'style_profile_id',
        'page_count',
        'source',
        'bound_agent_id',
        'platform_project_id',
        'visibility',
        'rev',
    )
    return {k: deck[k] for k in keep if k in deck}


def _deck_id(args: dict[str, Any]) -> int:
    try:
        return int(args['deck_id'])
    except (KeyError, TypeError, ValueError) as exc:
        raise errors.RequestError(msg="'deck_id' 必须是整数 id") from exc


def _page_id(args: dict[str, Any]) -> int:
    try:
        return int(args['page_id'])
    except (KeyError, TypeError, ValueError) as exc:
        raise errors.RequestError(msg="'page_id' 必须是整数 id") from exc


def _resolve_create_project_id(args: dict[str, Any]) -> str | None:
    """解析新建 Deck 的项目归属：显式入参优先，否则继承 AppCollab 工作会话上下文。

    显式 ``null`` 表示主人明确要求不挂项目，不能再回落上下文；普通分身调用无需知道或传入
    ``platform_project_id``，Runtime 注入的 ``_hasn_project_id`` 已由分发入口落进 ContextVar。
    """
    if 'platform_project_id' in args:
        raw = args.get('platform_project_id')
        if raw is None:
            return None
        return str(raw).strip() or None
    project_id = get_current_project_id()
    return project_id.strip() if project_id and project_id.strip() else None


async def _upsert_pages(db: Any, ctx: AgentContext, deck_id: int, pages_input: list[dict[str, Any]]) -> Any:
    """按 position 序数 upsert 多页（复刻 daemon write_pages）：逐页骨架校验，不合格进 rejected，
    合格页已有该位 → update_page，否则 create_page（status=generated）。existing 在循环前取一次。

    返回体只含**本次写入的那几页**的摘要，不再回显整个 deck 的全部页：写 1 页却回 30 页全量
    HTML，既是纯浪费也会随 deck 变长而越来越贵。`total_pages` 由 existing + 新建数直接算出，
    因此写完也不再多打一次全表读。
    """
    subject = _subject(ctx)
    # 先验 deck 归属/权限（不存在/跨户 → NotFound），而非逐页 fail。
    await deck_service.get_deck(db, subject=subject, deck_id=deck_id)
    existing = (await deck_service.list_pages(db, subject=subject, deck_id=deck_id))['items']
    by_position = {int(p['position']): p for p in existing}

    written: list[dict[str, Any]] = []
    created = 0
    rejected: list[dict[str, Any]] = []
    for page in sorted(pages_input, key=lambda p: int(p.get('position', 0))):
        position = int(page['position'])
        html = str(page.get('html') or '')
        reason = validate_page_skeleton(html)
        if reason is not None:
            rejected.append({'position': position, 'reason': reason})
            continue
        title = str(page.get('title') or '')
        notes = page.get('notes')
        occupant = by_position.get(position)
        if occupant is not None:
            saved = await deck_service.update_page(
                db,
                subject=subject,
                page_id=int(occupant['id']),
                fields={'title': title, 'html': html, 'notes': notes, 'status': 'generated'},
            )
        else:
            saved = await deck_service.create_page(
                db,
                subject=subject,
                deck_id=deck_id,
                position=position,
                title=title,
                html=html,
                notes=notes,
                status='generated',
            )
            created += 1
        written.append(_page_brief(saved))

    # register-on-write：写页即登记（覆盖 page.write / page.write_batch 两个 handler）。
    registration = await _register_deck_artifact(db, ctx, deck_id)
    return merge_resource_uri(
        {
            'written': len(written),
            'rejected': rejected,
            'pages': written,
            'total_pages': len(existing) + created,
        },
        registration,
    )


# ── handlers ─────────────────────────────────────────────────────────────────────
async def _h_create(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """新建空演示文稿（owner-scoped；create_deck 收 owner_id 而非 subject）。返回 deck_id。

    分身用工具建 deck 时**自动绑定调用它的分身为协作分身**（bound_agent_id=当前 agent）：
    首页「让分身生成」改为纯派发工作会话、不再预建带绑定的空 deck，绑定关系就落在这里——
    分身 `hasn.deck.create` 建好即是它自己名下的协作 deck，后续 generate/instruct 续接同一分身、
    webui 也能正确展示协作分身。身份取自 Agent JWT 解析的 ctx，绝不入请求体。
    """
    title = str(args.get('title') or '').strip() or '未命名演示文稿'
    deck = await deck_service.create_deck(
        db,
        owner_id=_owner_hasn_id(ctx),
        title=title,
        topic=(str(args['topic']).strip() if args.get('topic') else None),
        language=str(args.get('language') or 'zh'),
        source='agent',
        style_profile_id=(str(args['style_profile_id']).strip() if args.get('style_profile_id') else None),
        bound_agent_id=ctx.agent_hasn_id,
        platform_project_id=_resolve_create_project_id(args),
    )
    # register-on-write：建 deck 即登记进工作会话资源栏 / 分身产物 tab（不等 finalize）。
    registration = await _register_deck_artifact(db, ctx, int(deck['id']), title=title)
    return merge_resource_uri(
        {'deck_id': str(deck['id']), 'status': deck['status'], 'deck': _deck_brief(deck)}, registration
    )


async def _h_get(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """取 deck 详情。deck 本体回完整（outline / design_contract 是分身接着要用的输入）；
    页默认只回摘要 + notes，正文 HTML 要显式 `include_html=true` 才回——一份 30 页的 deck
    全量 HTML 有几百 KB，而分身多数时候只是想知道有哪些页、各页什么标题。"""
    subject = _subject(ctx)
    deck_id = _deck_id(args)
    include_html = bool(args.get('include_html') or False)
    deck = await deck_service.get_deck(db, subject=subject, deck_id=deck_id)
    pages = await deck_service.list_pages(db, subject=subject, deck_id=deck_id)
    items = [_page_brief(p, include_html=include_html, include_notes=True) for p in pages['items']]
    return {'deck': deck, 'pages': items}


async def _h_list(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    raw_project_id = args.get('platform_project_id')
    platform_project_id = (
        str(raw_project_id).strip() or None if raw_project_id is not None else None
    )
    result = await deck_service.list_accessible_decks(
        db,
        subject=_subject(ctx),
        platform_project_id=platform_project_id,
    )
    return {'decks': result['items'], 'total': result['total']}


async def _h_outline_set(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """写/改大纲：outline 存 `{'items': pages}`（与 daemon→云端 sync 形状一致），design_contract 独立列。"""
    fields: dict[str, Any] = {'outline': {'items': args['pages']}}
    if args.get('design_contract') is not None:
        fields['design_contract'] = args['design_contract']
    deck = await deck_service.update_deck(db, subject=_subject(ctx), deck_id=_deck_id(args), fields=fields)
    # register-on-write：写大纲即登记（标题取 deck 权威标题）。
    registration = await _register_deck_artifact(db, ctx, _deck_id(args), title=str(deck.get('title') or '') or None)
    # 只回 deck 摘要：刚写进去的 outline / design_contract 原样退回没有信息量。
    return merge_resource_uri({'deck': _deck_brief(deck)}, registration)


async def _h_page_write_batch(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await _upsert_pages(db, ctx, _deck_id(args), list(args.get('pages') or []))


async def _h_page_write(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    page = {'position': args['position'], 'html': args['html'], 'title': args.get('title'), 'notes': args.get('notes')}
    return await _upsert_pages(db, ctx, _deck_id(args), [page])


async def _h_page_edit(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """按 page_id 局部更新（仅传需改字段）。HTML 非空时做骨架校验（对齐 daemon edit_page 语义）。

    返回只回页摘要。此前回显整页 `html`，让「只改一个标题」也要付整页 HTML 的 token——
    分身实测因此绕开本工具、改用 write_batch 搬运整页，正是被这个回显逼的。
    """
    fields: dict[str, Any] = {}
    if args.get('html') is not None:
        html = str(args['html'])
        if html.strip():
            reason = validate_page_skeleton(html)
            if reason is not None:
                raise errors.RequestError(msg=f'页 HTML 骨架校验未通过：{reason}')
        fields['html'] = html
    if args.get('title') is not None:
        fields['title'] = str(args['title'])
    if args.get('notes') is not None:
        fields['notes'] = args['notes']
    page = await deck_service.update_page(db, subject=_subject(ctx), page_id=_page_id(args), fields=fields)
    # register-on-write：改某页即登记（deck_id 从被改页反查，title 由 helper 补取）。
    registration: ArtifactRegistration | None = None
    try:
        registration = await _register_deck_artifact(db, ctx, int(page['deck_id']))
    except (KeyError, TypeError, ValueError):
        # 返回体里没有可用的 deck_id——登记不了、URI 也算不出来，照 §3.2 省略 uri 字段。
        pass
    return merge_resource_uri({'page': _page_brief(page)}, registration)


async def _h_page_delete(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    page_id = _page_id(args)
    await deck_service.delete_page(db, subject=_subject(ctx), page_id=page_id)
    return {'deleted': True, 'page_id': str(page_id)}


async def _h_page_reorder(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    ordered = [int(pid) for pid in (args.get('page_ids') or [])]
    result = await deck_service.reorder_pages(
        db, subject=_subject(ctx), deck_id=_deck_id(args), ordered_page_ids=ordered
    )
    # 重排只改 position，回显整份 HTML 与本次动作完全无关。
    return {'pages': [_page_brief(p) for p in result['items']]}


async def _h_finalize(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """收尾：把演示文稿标记为「已完成」，由云端自动给主人发「演示文稿做好了」卡
    （分身不用、也不该自己发卡）；重复调不会重复发卡。

    完成卡不在业务事务内直接写 IM；这里与 deck 状态、产物登记同事务写 session outbox。
    独立 relay 提交后投递，进程崩溃可恢复。ready 重调仍使用稳定幂等键
    ``deck_complete:{deck_id}``，可自愈首次响应丢失且不会重复发卡；archived 不发。
    """
    deck_id = _deck_id(args)
    result = await deck_service.finalize_deck(db, subject=_subject(ctx), deck_id=deck_id)
    # register-on-write：收尾也登记（标题用 finalize 返回的权威标题）。
    registration = await _register_deck_artifact(db, ctx, deck_id, title=str(result.get('title') or '') or None)
    will_emit = result['status'] == 'ready'
    out: dict[str, Any] = merge_resource_uri(
        {
            'deck_id': str(deck_id),
            'status': result['status'],
            'finalized': result['changed'],
            'card_sent': will_emit,
        },
        registration,
    )
    if will_emit:
        from backend.app.hasn.service.hasn_sessions_service import (
            emit_deck_completion_card,
        )

        delivery = await emit_deck_completion_card(
            db,
            owner_id=_owner_hasn_id(ctx),
            agent_id=ctx.agent_hasn_id,
            deck_id=str(deck_id),
            title=str(result.get('title') or ''),
            summary=str(args.get('summary') or ''),
        )
        out['card_delivery_command_id'] = delivery.get('command_id')
        out['card_delivery_state'] = delivery.get('status')
    return out


async def _h_delete(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    deck_id = _deck_id(args)
    await deck_service.delete_deck(db, subject=_subject(ctx), deck_id=deck_id)
    return {'deleted': True, 'deck_id': str(deck_id)}


async def _h_style_list(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """列样式：只回挑选所需的 slug / label / description / source。

    完整 `design_contract` + `style_prompt` 每个风格都有上千字，内置 37 个一起回等于让分身为了
    挑一个 slug 读完整本样式手册。选定后用 `style.get` 取那一个的详情。
    """
    result = await deck_service.list_style_profiles(db, owner_id=_owner_hasn_id(ctx))
    styles = [
        {'slug': s['slug'], 'label': s['label'], 'description': s['description'], 'source': s['source']}
        for s in result['items']
    ]
    return {'styles': styles, 'total': len(styles)}


async def _h_style_get(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    style_id = str(args['style_id']).strip()
    result = await deck_service.list_style_profiles(db, owner_id=_owner_hasn_id(ctx))
    style = next((s for s in result['items'] if s.get('slug') == style_id), None)
    if style is None:
        raise errors.NotFoundError(msg=f'样式不存在：{style_id}')
    return {'style': style}


# ── schema 片段 ────────────────────────────────────────────────────────────────────
_DECK_ID = {'deck_id': {'type': 'string', 'minLength': 1}}
# 单页 HTML 字段说明（带硬规则，避免「通过校验却渲染空白」的常见坑）。服务端做骨架硬校验。
_HTML_DESC = (
    '单页 16:9 创意 HTML 片段（只传主体；引擎自动包骨架并注入 Tailwind/Chart.js/anime.js/PPT 运行时——'
    '勿传 <html>/<head>/<body>/<script src>/<link>/@font-face/完整文档）。'
    '图表：用 PPT.createChart(canvasOrSelector, config)，禁直接 new Chart()；'
    '且图表父容器必须有固定像素高（如 class="h-[300px]" 或 aspect 比例类）——'
    'flex-1/h-full/仅百分比/仅 min-height 会被量到约 0 高而渲染空白（导出/截图尤甚）；'
    '简单条形/占比图优先用纯 CSS（flex 高度比例）更稳。'
    '动画用 PPT.animate(targets, params)：scale 自动导出为 zoom，小幅 translateY:[正值,0] 自动导出为 rise，'
    '其他位移按运动方向导出为 fly-up/fly-down/fly-left/fly-right；'
    '需精确指定时可在元素写 data-anim="fade|wipe-up|wipe-down|wipe-left|wipe-right|rise|zoom|'
    'fly-up|fly-down|fly-left|fly-right" + data-anim-step="N" 归步；'
    '其中 fly-* 后缀表示运动方向，显式 data-anim 优先。'
    '这些效果会进入应用内放映并导出为 PPTX 原生入场动画；未知值才降级 fade。初始态必须可见'
    '（入场用 opacity:[0,1] 等参数，勿用 opacity-0/visibility:hidden 作默认）。'
)
# outline item / page item 细校验留给服务端（与本地 hasn-mcp 一致，宽松对象）。
_OUTLINE_ITEM = {'type': 'object', 'description': 'OutlineItem（title/layout_intent/... 由服务端宽松接收）'}
_PAGE_ITEM = {
    'type': 'object',
    'properties': {
        'position': {'type': 'integer', 'minimum': 0},
        'title': {'type': ['string', 'null']},
        'html': {'type': 'string', 'minLength': 1, 'description': _HTML_DESC},
        'notes': {'type': ['string', 'null']},
    },
    'required': ['position', 'html'],
}


# ── G6 资源权限门声明（doc32 §4·doc33 S1-3/S2-6）─────────────────────────────────────
# deck 工具经 MCP 直连面（server.py::call_tool 读 tool.resource_access）判权——deck manifest tools=[]，
# 这些声明不进 manifest 注册期校验，由下方 `_DeckTool.resource_access` 暴露给门。deck service 层原有
# subject+authorize 保留（防御纵深），门在 ask 审批前先按同一 `resolve_effective_permission` 内核多判一次。
# 页级操作按 `page_id`=deck_page 判（门据 page_id 反查**真实所属 deck** 再判，防「传我有权的 deck_id +
# 别人的 page_id」混淆代理）；deck_page 无自身 share、档位纯继承所属 deck。
_RA_DECK_VIEWER = [{'param': 'deck_id', 'type': 'deck', 'need': 'viewer'}]
_RA_DECK_EDITOR = [{'param': 'deck_id', 'type': 'deck', 'need': 'editor'}]
_RA_DECK_MANAGER = [{'param': 'deck_id', 'type': 'deck', 'need': 'manager'}]
_RA_PAGE_EDITOR = [{'param': 'page_id', 'type': 'deck_page', 'need': 'editor'}]


# ── 工具规格（action → name=hasn.deck.<action>）─────────────────────────────────────
_SPECS: list[dict[str, Any]] = [
    {
        'action': 'create',
        'write': True,
        'handler': _h_create,
        'desc': '新建空演示文稿（可带 title/topic/language/style_profile_id）。返回 deck_id。',
        'schema': {
            'type': 'object',
            'properties': {
                'title': {'type': ['string', 'null'], 'description': '标题（可选）'},
                'topic': {'type': ['string', 'null'], 'description': '主题（可选）'},
                'language': {'type': ['string', 'null'], 'description': '语言（默认 zh）'},
                'style_profile_id': {'type': ['string', 'null'], 'description': '引用样式 slug（见 style.list）'},
                'platform_project_id': {
                    'type': ['string', 'null'],
                    'description': '挂靠的平台项目 UUID（可选；省略时自动继承当前工作会话项目）',
                },
            },
        },
    },
    {
        'action': 'get',
        'write': False,
        'handler': _h_get,
        'desc': (
            '取 deck 详情（outline / design_contract + 全部页摘要：id/position/title/status/notes/html_length）。'
            '页正文默认不返回，确需读回原文时才传 include_html=true。确定性读。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                **_DECK_ID,
                'include_html': {
                    'type': ['boolean', 'null'],
                    'description': '是否返回每页完整 HTML（默认 false；整份 deck 的 HTML 很长，只在要读回原文时开）',
                },
            },
            'required': ['deck_id'],
        },
        'resource_access': _RA_DECK_VIEWER,
    },
    {
        'action': 'list',
        'write': False,
        'handler': _h_list,
        'desc': '列出可访问的 deck；默认不按工作会话项目收窄，可显式按平台项目筛选。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {
                'platform_project_id': {
                    'type': ['string', 'null'],
                    'description': '显式筛选的平台项目 UUID；省略时返回全部可访问 Deck',
                }
            },
        },
    },
    {
        'action': 'outline.set',
        'write': True,
        'handler': _h_outline_set,
        'desc': '写 / 改 deck 大纲（OutlineItem[]）+ 可选 design_contract。先定大纲再逐页写 HTML。',
        'schema': {
            'type': 'object',
            'properties': {
                **_DECK_ID,
                'pages': {'type': 'array', 'items': _OUTLINE_ITEM, 'description': '大纲项数组'},
                'design_contract': {'type': ['object', 'null'], 'description': '统一视觉契约（可选）'},
            },
            'required': ['deck_id', 'pages'],
        },
        'resource_access': _RA_DECK_EDITOR,
    },
    {
        'action': 'page.write_batch',
        'write': True,
        'handler': _h_page_write_batch,
        'desc': (
            '批量写多页 HTML（主力）。逐页骨架校验；不合格页进 rejected 让你重写，合格页落库。'
            '只回本次写入页的摘要（不回显你刚传的 HTML；html_length 可用来核对是否被截断）。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                **_DECK_ID,
                'pages': {'type': 'array', 'items': _PAGE_ITEM, 'description': '按序写入的页数组'},
            },
            'required': ['deck_id', 'pages'],
        },
        'resource_access': _RA_DECK_EDITOR,
    },
    {
        'action': 'page.write',
        'write': True,
        'handler': _h_page_write,
        'desc': (
            '写单页 HTML（按 position 序数 upsert，幂等）。骨架不合格则进 rejected。'
            '只回该页摘要，不回显你刚传的 HTML。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                **_DECK_ID,
                'position': {'type': 'integer', 'minimum': 0},
                'html': {'type': 'string', 'minLength': 1, 'description': _HTML_DESC},
                'title': {'type': ['string', 'null']},
                'notes': {'type': ['string', 'null']},
            },
            'required': ['deck_id', 'position', 'html'],
        },
        'resource_access': _RA_DECK_EDITOR,
    },
    {
        'action': 'page.edit',
        'write': True,
        'handler': _h_page_edit,
        'desc': (
            '编辑某页（按 page_id 局部更新；仅传需要改的字段）。HTML 非空时做骨架校验。'
            '只改标题 / 备注时就只传那一个字段，返回只回页摘要、不回显 HTML——'
            '改单个字段用本工具比用 page.write 重传整页便宜得多。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                **_DECK_ID,
                'page_id': {'type': 'string', 'minLength': 1},
                'html': {'type': ['string', 'null'], 'description': _HTML_DESC},
                'title': {'type': ['string', 'null']},
                'notes': {'type': ['string', 'null']},
            },
            'required': ['deck_id', 'page_id'],
        },
        'resource_access': _RA_PAGE_EDITOR,
    },
    {
        'action': 'page.delete',
        'write': True,
        'handler': _h_page_delete,
        'desc': '删除某页（删后自动重排剩余页序）。破坏性。',
        'schema': {
            'type': 'object',
            'properties': {**_DECK_ID, 'page_id': {'type': 'string', 'minLength': 1}},
            'required': ['deck_id', 'page_id'],
        },
        'resource_access': _RA_PAGE_EDITOR,
    },
    {
        'action': 'page.reorder',
        'write': True,
        'handler': _h_page_reorder,
        'desc': '重排页序：给定按目标顺序排列的全部页 id。',
        'schema': {
            'type': 'object',
            'properties': {
                **_DECK_ID,
                'page_ids': {'type': 'array', 'items': {'type': 'string'}, 'description': '按目标顺序排列的全部页 id'},
            },
            'required': ['deck_id', 'page_ids'],
        },
        'resource_access': _RA_DECK_EDITOR,
    },
    {
        'action': 'finalize',
        'write': True,
        'handler': _h_finalize,
        'desc': (
            '收尾：写完最后一页后**调一次**，把演示文稿标记为「已完成」。'
            '首次收尾时系统会自动给主人发一张「演示文稿做好了」卡片——'
            '**你不用、也不要自己发卡/发消息通知主人**。可带 summary 一句话概述。幂等：重复调不会重复发卡。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                **_DECK_ID,
                'summary': {'type': ['string', 'null'], 'description': '一句话概述这份演示文稿（作为卡片描述，可选）'},
            },
            'required': ['deck_id'],
        },
        'resource_access': _RA_DECK_EDITOR,
    },
    {
        'action': 'delete',
        'write': True,
        'handler': _h_delete,
        'desc': '删除演示文稿（软删，可同步撤销）。破坏性。',
        'schema': {'type': 'object', 'properties': dict(_DECK_ID), 'required': ['deck_id']},
        'resource_access': _RA_DECK_MANAGER,
    },
    {
        'action': 'style.list',
        'write': False,
        'handler': _h_style_list,
        'desc': '列可复用样式（系统内置 37 风格 ∪ 本 owner 自定义），只回 slug/label/description；详情用 style.get。确定性读。',
        'schema': {'type': 'object', 'properties': {}},
    },
    {
        'action': 'style.get',
        'write': False,
        'handler': _h_style_get,
        'desc': '取单个样式（StyleProfile）：含 design_contract + style_prompt。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {'style_id': {'type': 'string', 'minLength': 1, 'description': '样式 slug'}},
            'required': ['style_id'],
        },
    },
]


class _DeckTool(BaseTool):
    """deck 域单 struct + spec 派发（对齐 plan.py/task.py/workflow.py，Rust DeckOp 枚举派发）。"""

    def __init__(self, spec: dict[str, Any]) -> None:
        self._name = f'{NAMESPACE}.{spec["action"]}'
        self._desc = spec['desc']
        self._input_schema = spec['schema']
        self._write = bool(spec['write'])
        self._handler: Handler = spec['handler']
        # G6 资源权限门声明（无则 None，门跳过；server.call_tool 经 getattr 读取）。
        self._resource_access: list[dict[str, Any]] | None = spec.get('resource_access')

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return NAMESPACE

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return self._desc

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def required_scopes(self) -> list[str]:
        # 读类无 scope；写类统一 deck:manage（跨仓与本地 hasn-mcp deck.rs 对齐）。
        return [SCOPE_MANAGE] if self._write else []

    @property
    def resource_access(self) -> list[dict[str, Any]] | None:
        """G6 资源权限门声明（doc32 §5·doc33 S2-6）。server.call_tool 的门经此判「分身能不能动该 deck/页」。
        deck 走 MCP 直连面（deck manifest tools=[]），故声明只在此暴露、不进 manifest 注册期校验。"""
        return self._resource_access

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        # 维度① 三态由 server.call_tool 统一判定（D3），工具内不二次校验。
        if self._write:
            async with async_db_session.begin() as db:
                result = await self._handler(db, agent_context, arguments)
            # LFRT 刀4：提交后 bump（best-effort），主人在线节点秒级重拉 deck 镜像。
            await _bump_decks_sync(agent_context.owner_hasn_id)
            return result
        async with async_db_session() as db:
            return await self._handler(db, agent_context, arguments)


DECK_TOOLS: list[_DeckTool] = [_DeckTool(spec) for spec in _SPECS]
