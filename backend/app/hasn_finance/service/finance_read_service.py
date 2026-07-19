"""金融投研 6 类产物 + watchlist 的 owner 端**下行读**（05 §3.2.1）。

与 [`finance_sync_service`] 成对：那边是 daemon outbox 的上行投影，这边是 daemon read-through 的
下行回源。**没有第四个后台 worker**——read-through 发生在 daemon 的读 handler 内（05 §3.2.1 结语）。

三条硬规则（评审必查）：

1. **owner 隔离**：所有查询必带 `owner_id`，且该值只能来自鉴权上下文（`resolve_owner`）。客户端
   传入的 owner 一律不可信——本模块不接受 owner 入参以外的身份来源。
2. **不下发服务端内部幂等元数据**（§3.2.1 规则 7）：`local_ref` / `last_client_op_id` 绝不出现在
   响应里。前者是 daemon 侧本地行 id（**本地 ID 永不上 URI**，B 设备自行生成本地镜像 id），后者只
   用于响应丢失后的幂等回放。下行只给 server id / revision / 业务字段。
3. **tombstone 可下行**：`status='deleted'` 的行默认不返回（产品语义），但 daemon 同步需要据它删本地
   镜像 → 显式 `include_deleted=True` 才带上。**不能靠「不在结果里」推断删除**（那和翻页/过滤无从区分）。

隐私红线（05 §3.1.5）：云端表本就**没有** `source_file_ref`/`source_content_hash`——原始对账单绝对
路径只在本地 SQLite。故本模块无需额外剔除，只在 `test_finance_read_service.py` 用守卫测试钉死
「这两个字段永远不存在于任何 finance 表」，防后人加列时破线。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from backend.app.hasn_finance.model.backtest_report import BacktestReport
from backend.app.hasn_finance.model.research_report import ResearchReport
from backend.app.hasn_finance.model.shadow_account import ShadowAccount
from backend.app.hasn_finance.model.strategy import Strategy
from backend.app.hasn_finance.model.trade_review import TradeReview
from backend.app.hasn_finance.model.watch_briefing import WatchBriefing
from backend.app.hasn_finance.model.watchlist import Watchlist
from backend.common.exception import errors

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# resource_kind → ORM 类（含 watchlist；watchlist 非产物、不登记，但同样是 owner 隔离的下行读）
PRODUCT_MODELS: dict[str, Any] = {
    'finance.research_report': ResearchReport,
    'finance.strategy': Strategy,
    'finance.backtest_report': BacktestReport,
    'finance.trade_review': TradeReview,
    'finance.shadow_account': ShadowAccount,
    'finance.watch_briefing': WatchBriefing,
    'finance.watchlist': Watchlist,
}

# 服务端内部幂等元数据，任何下行响应都不含（§3.2.1 规则 7）
_INTERNAL_FIELDS = frozenset({'local_ref', 'last_client_op_id'})

# 列表页不返回的重字段：正文/源码/曲线/逐笔——详情才给。
# 这不是隐私裁剪（主人读自己的数据），纯粹是别让列表 payload 拖到几 MB。
_LIST_HEAVY_FIELDS: dict[str, frozenset[str]] = {
    'finance.research_report': frozenset({'body_md', 'findings_json'}),
    'finance.strategy': frozenset({'code_py'}),
    'finance.backtest_report': frozenset({'equity_curve_json', 'trades_json', 'metrics_json'}),
    'finance.trade_review': frozenset({'body_md', 'findings_json'}),
    'finance.shadow_account': frozenset({'profile_json', 'behaviors_json'}),
    'finance.watch_briefing': frozenset({'body_md'}),
    'finance.watchlist': frozenset(),
}

# 各类允许的过滤列白名单（只认这些 query 参数；未声明的键静默忽略，不让客户端拿任意列当过滤面）
_FILTERABLE: dict[str, frozenset[str]] = {
    'finance.research_report': frozenset({'symbol', 'market', 'verdict'}),
    'finance.strategy': frozenset({'market', 'source', 'platform_project_id'}),
    'finance.backtest_report': frozenset({'strategy_id'}),
    'finance.trade_review': frozenset({'shadow_account_id'}),
    'finance.shadow_account': frozenset({'broker', 'platform_project_id'}),
    'finance.watch_briefing': frozenset({'briefing_date', 'trigger'}),
    'finance.watchlist': frozenset({'symbol', 'market'}),
}

# 单页上限：防客户端要 limit=100000 把整库拖走
_MAX_LIMIT = 200


def _model_of(resource_kind: str) -> Any:
    """resource_kind → ORM 类；认不出直接抛，不回落到某个「默认类」（回落=返回另一类资源，比报错更难查）。"""
    model_cls = PRODUCT_MODELS.get(resource_kind)
    if model_cls is None:
        raise errors.NotFoundError(msg=f'未知的金融资源类型: {resource_kind}')
    return model_cls


def _serialize(row: Any, *, exclude: frozenset[str]) -> dict[str, Any]:
    """ORM 行 → dict，逐列取值并剔除 exclude。

    遍历 `__table__.columns` 而非 schema：新加的列自动带上，不会因为忘了同步 schema 就静默丢字段。
    代价是新加的**内部**列必须记得进 `_INTERNAL_FIELDS`——守卫测试钉死了这条。
    """
    return {col.name: getattr(row, col.name) for col in row.__table__.columns if col.name not in exclude}


def _apply_filters(stmt: Any, model_cls: Any, resource_kind: str, filters: dict[str, Any] | None) -> Any:
    """按白名单 apply 过滤条件；非白名单键/空值一律忽略。"""
    allowed = _FILTERABLE.get(resource_kind, frozenset())
    for key, value in (filters or {}).items():
        # 只跳过「没传」（None）和空串（FastAPI 里 ?symbol= 会给空串）——不用 falsy 判断，
        # 否则 strategy_id=0 之类的合法零值会被当成「没传」而静默忽略。
        if key in allowed and value not in (None, ''):
            stmt = stmt.where(getattr(model_cls, key) == value)
    return stmt


class FinanceReadService:
    """finance 下行读：owner 隔离的 list/get，供 daemon read-through 回源。"""

    async def list_resources(
        self,
        db: AsyncSession,
        *,
        resource_kind: str,
        owner_id: str,
        limit: int = 50,
        offset: int = 0,
        include_deleted: bool = False,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """列 owner 名下某类资源（新建在前）。

        返回 `{items, has_more}`：多取一条探测下一页，避免为分页额外跑一次 COUNT（列表页不需要精确总数）。
        `include_deleted=True` 时带上 tombstone（daemon 同步据 `status='deleted'` 删本地镜像）。
        """
        model_cls = _model_of(resource_kind)
        capped = max(1, min(limit, _MAX_LIMIT))

        stmt = select(model_cls).where(model_cls.owner_id == owner_id)
        if not include_deleted:
            stmt = stmt.where(model_cls.status != 'deleted')
        stmt = _apply_filters(stmt, model_cls, resource_kind, filters)
        # 多取一条只为判断 has_more，不返给客户端
        stmt = stmt.order_by(model_cls.id.desc()).limit(capped + 1).offset(max(0, offset))

        rows = (await db.execute(stmt)).scalars().all()
        has_more = len(rows) > capped
        exclude = _INTERNAL_FIELDS | _LIST_HEAVY_FIELDS.get(resource_kind, frozenset())
        return {
            'items': [_serialize(row, exclude=exclude) for row in rows[:capped]],
            'has_more': has_more,
        }

    async def get_resource(self, db: AsyncSession, *, resource_kind: str, owner_id: str, pk: int) -> dict[str, Any]:
        """取单条详情（全字段，除内部幂等元数据）。

        owner 不匹配一律 404（不是 403）——不让越权探测者据状态码区分「不存在」与「存在但不是你的」。
        tombstone 照常返回：daemon 据 `status` 判定本地镜像该删，客户端据它显示「已删除」。
        """
        model_cls = _model_of(resource_kind)
        stmt = select(model_cls).where(model_cls.id == pk, model_cls.owner_id == owner_id)
        row = (await db.execute(stmt)).scalars().first()
        if row is None:
            raise errors.NotFoundError(msg='资源不存在')
        return _serialize(row, exclude=_INTERNAL_FIELDS)


finance_read_service: FinanceReadService = FinanceReadService()
