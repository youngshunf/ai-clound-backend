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
from backend.app.mcp.tools.base import BaseTool
from backend.common.exception import errors
from backend.database.db import async_db_session

logger = logging.getLogger(__name__)

NAMESPACE = 'hasn.deck'
SCOPE_MANAGE = 'deck:manage'

Handler = Callable[[Any, AgentContext, dict[str, Any]], Awaitable[Any]]


def _require_owner_hasn_id(ctx: AgentContext) -> str:
    """从已鉴权分身上下文取得主人身份，缺失时拒绝访问主人隔离数据。"""
    owner_hasn_id = ctx.owner_hasn_id
    if not owner_hasn_id:
        raise RuntimeError('deck: Agent 凭证缺少 owner_hasn_id')
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
        session_id=ctx.session_id,
        agent_hasn_id=ctx.agent_hasn_id,
        owner_hasn_id=_require_owner_hasn_id(ctx),
        title=resolved_title or '演示文稿',
        source_tool=f'{NAMESPACE}.write',
    )


async def _run_deck_post_commit(post: dict[str, Any]) -> None:
    """写事务**提交后**的 deck 副作用（当前仅 finalize 完成卡）。独立会话——`route_message`
    自管 `db.commit()`，绝不能塞进 `execute` 的 `begin()` 事务里（否则 route_message 的提交会提前
    结束该事务，收尾时触发「Can't operate on closed transaction inside context manager」）。best-effort。
    """
    if post.get('kind') != 'deck_completion_card':
        return
    try:
        from backend.app.hasn.service.hasn_sessions_service import emit_deck_completion_card

        async with async_db_session() as db:
            await emit_deck_completion_card(
                db,
                owner_id=post['owner_id'],
                agent_id=post['agent_id'],
                deck_id=post['deck_id'],
                title=post.get('title') or '',
                summary=post.get('summary') or '',
            )
    except Exception as e:
        logger.warning('[deck] finalize 完成卡投递失败（非致命）: %s', e)


def _subject(ctx: AgentContext) -> Subject:
    return Subject.agent(ctx.agent_hasn_id, _require_owner_hasn_id(ctx))


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


async def _upsert_pages(db: Any, ctx: AgentContext, deck_id: int, pages_input: list[dict[str, Any]]) -> Any:
    """按 position 序数 upsert 多页（复刻 daemon write_pages）：逐页骨架校验，不合格进 rejected，
    合格页已有该位 → update_page，否则 create_page（status=generated）。existing 在循环前取一次。"""
    subject = _subject(ctx)
    # 先验 deck 归属/权限（不存在/跨户 → NotFound），而非逐页 fail。
    await deck_service.get_deck(db, subject=subject, deck_id=deck_id)
    existing = (await deck_service.list_pages(db, subject=subject, deck_id=deck_id))['items']
    by_position = {int(p['position']): p for p in existing}

    written = 0
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
            await deck_service.update_page(
                db,
                subject=subject,
                page_id=int(occupant['id']),
                fields={'title': title, 'html': html, 'notes': notes, 'status': 'generated'},
            )
        else:
            await deck_service.create_page(
                db,
                subject=subject,
                deck_id=deck_id,
                position=position,
                title=title,
                html=html,
                notes=notes,
                status='generated',
            )
        written += 1

    pages_after = (await deck_service.list_pages(db, subject=subject, deck_id=deck_id))['items']
    # register-on-write：写页即登记（覆盖 page.write / page.write_batch 两个 handler）。
    registration = await _register_deck_artifact(db, ctx, deck_id)
    return merge_resource_uri({'written': written, 'rejected': rejected, 'pages': pages_after}, registration)


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
        owner_id=_require_owner_hasn_id(ctx),
        title=title,
        topic=(str(args['topic']).strip() if args.get('topic') else None),
        language=str(args.get('language') or 'zh'),
        source='agent',
        style_profile_id=(str(args['style_profile_id']).strip() if args.get('style_profile_id') else None),
        bound_agent_id=ctx.agent_hasn_id,
    )
    # register-on-write：建 deck 即登记进工作会话资源栏 / 分身产物 tab（不等 finalize）。
    registration = await _register_deck_artifact(db, ctx, int(deck['id']), title=title)
    return merge_resource_uri({'deck_id': str(deck['id']), 'status': deck['status'], 'deck': deck}, registration)


async def _h_get(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    subject = _subject(ctx)
    deck_id = _deck_id(args)
    deck = await deck_service.get_deck(db, subject=subject, deck_id=deck_id)
    pages = await deck_service.list_pages(db, subject=subject, deck_id=deck_id)
    return {'deck': deck, 'pages': pages['items']}


async def _h_list(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    result = await deck_service.list_accessible_decks(db, subject=_subject(ctx))
    return {'decks': result['items'], 'total': result['total']}


async def _h_outline_set(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """写/改大纲：outline 存 `{'items': pages}`（与 daemon→云端 sync 形状一致），design_contract 独立列。"""
    fields: dict[str, Any] = {'outline': {'items': args['pages']}}
    if args.get('design_contract') is not None:
        fields['design_contract'] = args['design_contract']
    deck = await deck_service.update_deck(db, subject=_subject(ctx), deck_id=_deck_id(args), fields=fields)
    # register-on-write：写大纲即登记（标题取 deck 权威标题）。
    registration = await _register_deck_artifact(db, ctx, _deck_id(args), title=str(deck.get('title') or '') or None)
    return merge_resource_uri({'deck': deck}, registration)


async def _h_page_write_batch(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await _upsert_pages(db, ctx, _deck_id(args), list(args.get('pages') or []))


async def _h_page_write(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    page = {'position': args['position'], 'html': args['html'], 'title': args.get('title'), 'notes': args.get('notes')}
    return await _upsert_pages(db, ctx, _deck_id(args), [page])


async def _h_page_edit(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """按 page_id 局部更新（仅传需改字段）。HTML 非空时做骨架校验（对齐 daemon edit_page 语义）。"""
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
    return merge_resource_uri({'page': page}, registration)


async def _h_page_delete(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    page_id = _page_id(args)
    await deck_service.delete_page(db, subject=_subject(ctx), page_id=page_id)
    return {'deleted': True, 'page_id': str(page_id)}


async def _h_page_reorder(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    ordered = [int(pid) for pid in (args.get('page_ids') or [])]
    result = await deck_service.reorder_pages(
        db, subject=_subject(ctx), deck_id=_deck_id(args), ordered_page_ids=ordered
    )
    return {'pages': result['items']}


async def _h_finalize(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """收尾：把演示文稿标记为「已完成」，由云端自动给主人发「演示文稿做好了」卡
    （分身不用、也不该自己发卡）；重复调不会重复发卡。

    ⚠️ 完成卡**不在此处直接发**——`emit_deck_completion_card` 内部经 `route_message` 会
    `db.commit()`，若在 `execute` 的 `begin()` 事务里调用，会提前结束该事务，收尾提交时触发
    「Can't operate on closed transaction inside context manager」。这里只 finalize + register-on-write，
    完成卡打包成 `_post_commit` 标记，由 `execute` 在写事务**提交后**的独立会话里投递。

    发卡条件是 **status=ready 即排投递**（不只 changed 首次转换）：投递自带
    `local_id=deck_complete:{deck_id}` 幂等（hasn_messages 唯一索引，`route_message` 命中即返回
    原消息、绝不二次发卡），故 ready 重调是**自愈补发**——治「首次收尾时完成卡投递失败
    （旧版事务 bug / route 瞬时失败）后被 changed=False 守卫卡死、卡永久丢失」
    （2026-07-11/12 生产 deck 完成卡丢失事故）。archived 不发。
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
        out['_post_commit'] = {
            'kind': 'deck_completion_card',
            'deck_id': str(deck_id),
            'title': str(result.get('title') or ''),
            'summary': str(args.get('summary') or ''),
            'owner_id': _require_owner_hasn_id(ctx),
            'agent_id': ctx.agent_hasn_id,
        }
    return out


async def _h_delete(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    deck_id = _deck_id(args)
    await deck_service.delete_deck(db, subject=_subject(ctx), deck_id=deck_id)
    return {'deleted': True, 'deck_id': str(deck_id)}


async def _h_style_list(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    result = await deck_service.list_style_profiles(db, owner_id=_require_owner_hasn_id(ctx))
    return {'styles': result['items']}


async def _h_style_get(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    style_id = str(args['style_id']).strip()
    result = await deck_service.list_style_profiles(db, owner_id=_require_owner_hasn_id(ctx))
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
    '动画用 PPT.animate(targets, params)；初始态必须可见'
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
            },
        },
    },
    {
        'action': 'get',
        'write': False,
        'handler': _h_get,
        'desc': '取 deck 详情（含全部页摘要 / outline / design_contract）。确定性读。',
        'schema': {'type': 'object', 'properties': dict(_DECK_ID), 'required': ['deck_id']},
        'resource_access': _RA_DECK_VIEWER,
    },
    {
        'action': 'list',
        'write': False,
        'handler': _h_list,
        'desc': '列出可访问的 deck（我的 ∪ 共享给我/我分身的 ∪ 企业可见）。确定性读。',
        'schema': {'type': 'object', 'properties': {}},
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
        'desc': '批量写多页 HTML（主力）。逐页骨架校验；不合格页进 rejected 让你重写，合格页落库。',
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
        'desc': '写单页 HTML（按 position 序数 upsert，幂等）。骨架不合格则进 rejected。',
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
        'desc': '编辑某页（按 page_id 局部更新；仅传需要改的字段）。HTML 非空时做骨架校验。',
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
        'desc': '列可复用样式（系统内置 37 风格 ∪ 本 owner 自定义）。确定性读。',
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
            # 写事务提交后的副作用（如 finalize 完成卡）在独立会话里做——route_message 自管 commit，
            # 不能嵌进上面的 begin() 事务（否则收尾提交撞「closed transaction」）。
            if isinstance(result, dict):
                post = result.pop('_post_commit', None)
                if post is not None:
                    await _run_deck_post_commit(post)
            return result
        async with async_db_session() as db:
            return await self._handler(db, agent_context, arguments)


DECK_TOOLS: list[_DeckTool] = [_DeckTool(spec) for spec in _SPECS]
