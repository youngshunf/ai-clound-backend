"""语义事实云端镜像服务。

承接 hasn-node 事实上行与主脑合并闸所需的云端镜像查询。分身日常记忆工具只读写本地
权威；云端不再暴露记忆工具，也不在本服务提供事实直写入口。

设计要点（与 doc16 §10 强约束对齐）：
- **owner 隔离强制**：所有读取以 `owner_id`（Agent JWT/MCP Key 解析出的 owner_hasn_id）为
  硬边界，身份绝不入请求体。
- **主体规约**：`agent_self` 必带 `agent_id` 且 `subject_id == agent_id`；`owner` 的
  `subject_id == owner_id`；`peer`/`world` 由调用方给 `subject_id`（agent_id 必空）。
  与表 CHECK 约束（`ck_semantic_fact_agent_id` / `ck_semantic_fact_world_scope`）一致。
- **object 串字段**：与本地 crate 双端一致，`object_json` 存 JSON 字符串；读出反序列化为
  `object`。时间 epoch ms（与本地 BigInteger 一致）。
- 只读方法默认只取**生效事实**（doc19 §3.4，见 `effective_fact_condition`），被替代/撤回的
  事实与「裁决仍然有效的」被合并/矛盾事实都不喂召回。

doc19 S3（19-多节点记忆分层与分身自治整理设计.md §3.2/§3.4/§8.2）：
- 生效可见性 = `status='active'` 且（未裁决 或 裁决所依据的 revision 已过期）。
  后半句是并发护栏：事实被裁决后又被本地整理或主人修改（revision 前进），旧裁决自动失效、
  事实重新可见，留待下轮合并重裁——竞态从「谁覆盖谁」变成「裁决过期作废」，不需要锁。

doc19 S2（同文档 §6 项目记忆 · §4.3/§8.2）：
- **项目记忆** = `subject_kind='world'` + `scope_kind='project'` + `scope_id=<云端项目 UUID>`，
  槽位与 CHECK 早已存在，零 schema 变更（D-5）；
- **读不收窄**（铁律「项目轴写继承·读不收窄」）：`search`/`recall`/`list` 拿到 `project_id`
  后做**并集**检索——排掉的只有**别的项目**的专属事实，全局常识与其它作用域一律照常可见；
- `supersedes_hint` 从 S3 的「塞 source_refs_json」收敛为**正式列**（§8.2 增列汇总本就把它列为
  双端增列；塞 JSON 串检索不到，合并规则层没法按列取）。
"""

from __future__ import annotations

import json

from typing import TYPE_CHECKING, Any, cast

import sqlalchemy as sa

from backend.app.hasn_memory.model.semantic_fact import SemanticFact

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import ColumnElement

_VALID_SUBJECT_KINDS = frozenset({'owner', 'agent_self', 'peer', 'world'})
_VALID_SCOPE_KINDS = frozenset({'global', 'workspace', 'project', 'task', 'conversation', 'topic'})
_MAX_LIMIT = 200


def effective_fact_condition() -> ColumnElement[bool]:
    """生效可见性判据（doc19 §3.4，注入 / 检索默认过滤）。

    ``status='active'`` 且（``merge_verdict IS NULL`` 或 ``merge_judged_revision`` 与当前
    ``revision`` 不同）。

    后半句是**并发护栏**，不是可选优化：合并把某条标成 `merged_into` / `disputed` 之后，
    该事实又被 origin 节点的分身整理、或被主人确认（§4.6，主人写使 revision 前进），旧裁决
    **自动失效**、事实立即重新可见，留待下轮合并重裁。「合并 vs 本地整理」的竞态因此从
    「谁覆盖谁」退化成「裁决过期作废」，不需要任何锁。

    ``IS DISTINCT FROM`` 而非 ``!=``：`merge_judged_revision` 可空，SQL 三值逻辑下
    ``NULL != 1`` 求值为 NULL（该行会被整体过滤掉），只有 IS DISTINCT FROM 才把
    「有裁决但没记依据 revision」的行按「裁决不可信 → 事实可见」处理。
    """
    return sa.and_(
        SemanticFact.status == 'active',
        sa.or_(
            SemanticFact.merge_verdict.is_(None),
            SemanticFact.merge_judged_revision.is_distinct_from(SemanticFact.revision),
        ),
    )


def scope_visibility_condition(
    *,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    project_id: str | None = None,
) -> ColumnElement[bool] | None:
    """作用域过滤条件（doc19 §6.2 项目记忆并集检索）。返回 None 表示不加任何 scope 条件。

    三档，按优先级：

    1. **调用方显式收窄**（给了 `scope_kind` 和/或 `scope_id`）→ 按显式条件过滤。分身明确说
       「只要这个作用域的」就照做，项目语境不再插手；
    2. **未显式收窄 + 有项目语境** → 并集检索。条件是
       ``scope_kind != 'project' OR scope_id = <当前项目>``，即**只排掉别的项目的专属事实**：
       当前项目的事实、`global` 全局常识、以及 `topic`/`workspace`/`task`/`conversation` 等
       非项目作用域的事实**全部照常可见**。
       ⚠️ 这里是**故意**比「project ∪ global」更宽的：`world` 主体受表 CHECK
       `ck_semantic_fact_world_scope` 约束**不许**用 `global` 作用域，全局性的世界知识只能落
       `topic`/`workspace`。若按字面只留 `project ∪ global`，分身一进项目就再也看不见任何
       world 事实——恰恰把 §6.2 要保住的「全局常识」全滤没了，直接违反铁律
       「项目轴写继承·**读不收窄**」。故判据写成「排他项目」而非「只留两档」；
    3. 都没有 → None，不加条件（全量可见，与本切片之前的行为完全一致）。

    `scope_id` 列 NOT NULL，无三值逻辑坑，可直接用 ``!=`` / ``==``。
    """
    explicit: list[ColumnElement[bool]] = []
    if scope_kind in _VALID_SCOPE_KINDS:
        explicit.append(SemanticFact.scope_kind == scope_kind)
    if scope_id:
        explicit.append(SemanticFact.scope_id == scope_id)
    if explicit:
        return sa.and_(*explicit)
    if project_id:
        return sa.or_(
            SemanticFact.scope_kind != 'project',
            SemanticFact.scope_id == project_id,
        )
    return None


def _project_first_order(project_id: str | None) -> list[ColumnElement[Any]]:
    """并集检索的排序前缀（doc19 §6.2）：**项目专属事实排在全局/其它作用域之前**。

    在项目里干活时，「这个项目怎么约定的」比「一般来说怎么样」更该先被看见；同档内仍按
    `updated_at` 倒序。无项目语境 → 空前缀，排序与本切片之前完全一致。
    """
    if not project_id:
        return []
    return [
        sa.case(
            (sa.and_(SemanticFact.scope_kind == 'project', SemanticFact.scope_id == project_id), 0),
            else_=1,
        )
    ]


def _loads(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw  # 历史脏数据：原样返回，不崩


def _serialize(fact: SemanticFact) -> dict[str, Any]:
    return {
        'fact_id': fact.fact_id,
        'owner_id': fact.owner_id,
        'agent_id': fact.agent_id,
        'subject_kind': fact.subject_kind,
        'subject_id': fact.subject_id,
        'scope_kind': fact.scope_kind,
        'scope_id': fact.scope_id,
        'predicate': fact.predicate,
        'object': _loads(cast('str', fact.object_json)),
        'confidence': fact.confidence,
        'status': fact.status,
        'rationale': fact.rationale,
        'created_at': fact.created_at,
        'updated_at': fact.updated_at,
        # doc19 §3.2/§3.4：溯源与裁决 overlay 随读返回——分身据此一眼看出哪些它能改（§7.2 editable）
        'origin_kind': fact.origin_kind,
        'origin_node_id': fact.origin_node_id,
        'origin_agent_id': fact.origin_agent_id,
        'revision': fact.revision,
        'merge_verdict': fact.merge_verdict,
        'merge_verdict_run': fact.merge_verdict_run,
        'merge_judged_revision': fact.merge_judged_revision,
        'valid_until': fact.valid_until,
        # doc19 §4.3：本人纠正指向随读返回——合并规则层与主人复盘都要看得见「这条想取代谁」
        'supersedes_hint': fact.supersedes_hint,
    }


class SemanticFactService:
    """语义事实云端只读查询面（owner 隔离强制）。

    事实业务写入只允许来自 ``fact_uplink_service``；合并派生片只允许由合并闸写入。本服务
    不再提供直写入口，避免重新长出绕过节点溯源、outbox 与 revision 守卫的第二写面。
    """

    @staticmethod
    async def search_facts(
        db: AsyncSession,
        *,
        owner_id: str,
        query: str,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """按文本在 predicate/object_json 上模糊搜索 owner 名下**生效**事实。

        doc18 S2：可选 `subject_id` 限定主体——「按人 + 关键词」组合检索（如查某 peer 关于某话题的事实）。
        doc19 §3.4：生效判据含合并裁决 overlay，裁决过期即自动重新可见。
        doc19 §6.2：新增 `scope_kind`/`scope_id` 显式过滤 + `project_id` 项目并集检索
        （判据见 `scope_visibility_condition`，项目专属事实排前）。
        """
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        stmt = sa.select(SemanticFact).where(
            SemanticFact.owner_id == owner_id,
            effective_fact_condition(),
        )
        if subject_kind in _VALID_SUBJECT_KINDS:
            stmt = stmt.where(SemanticFact.subject_kind == subject_kind)
        if subject_id:
            stmt = stmt.where(SemanticFact.subject_id == subject_id)
        q = (query or '').strip()
        if q:
            like = f'%{q}%'
            stmt = stmt.where(sa.or_(SemanticFact.predicate.ilike(like), SemanticFact.object_json.ilike(like)))
        scope_cond = scope_visibility_condition(scope_kind=scope_kind, scope_id=scope_id, project_id=project_id)
        if scope_cond is not None:
            stmt = stmt.where(scope_cond)
        stmt = stmt.order_by(*_project_first_order(project_id), SemanticFact.updated_at.desc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize(r) for r in rows]

    @staticmethod
    async def recall_facts(
        db: AsyncSession,
        *,
        owner_id: str,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        query: str | None = None,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        project_id: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """召回某主体/作用域下的**生效**事实（供注入），按 updated_at 倒序。

        doc18 S2：可选 `query` 词项过滤（predicate/object ILIKE，与 search 同实现）——补上「按人 + 关键词」召回。
        doc19 §3.4：被合并裁决为 merged_into / disputed 且裁决仍有效的事实**不进注入**。
        doc19 §6.2：`project_id` 触发项目并集检索（排他项目、留全局与其它作用域），项目专属事实排前。
        """
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        stmt = sa.select(SemanticFact).where(
            SemanticFact.owner_id == owner_id,
            effective_fact_condition(),
            SemanticFact.confidence >= float(min_confidence),
        )
        if subject_kind in _VALID_SUBJECT_KINDS:
            stmt = stmt.where(SemanticFact.subject_kind == subject_kind)
        if subject_id:
            stmt = stmt.where(SemanticFact.subject_id == subject_id)
        q = (query or '').strip()
        if q:
            like = f'%{q}%'
            stmt = stmt.where(sa.or_(SemanticFact.predicate.ilike(like), SemanticFact.object_json.ilike(like)))
        scope_cond = scope_visibility_condition(scope_kind=scope_kind, scope_id=scope_id, project_id=project_id)
        if scope_cond is not None:
            stmt = stmt.where(scope_cond)
        stmt = stmt.order_by(*_project_first_order(project_id), SemanticFact.updated_at.desc()).limit(limit)
        rows = (await db.execute(stmt)).scalars().all()
        return [_serialize(r) for r in rows]

    @staticmethod
    async def list_facts(
        db: AsyncSession,
        *,
        owner_id: str,
        subject_kind: str | None = None,
        agent_id: str | None = None,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        project_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页列 owner 名下**生效**事实（默认全主体；可按 subject_kind / agent_id / scope 过滤）。

        doc19 §3.4：与 search/recall 同一判据，避免「列表里有、召回里没有」的口径分裂。
        doc19 §6.2：`scope_kind`/`scope_id` 显式过滤 + `project_id` 项目并集检索，同样与另两条读路
        同判据同排序——三者口径一旦分裂，分身在项目里就会「列表能翻到、召回却拿不到」。
        """
        limit = max(1, min(_MAX_LIMIT, int(limit)))
        offset = max(0, int(offset))
        base = sa.select(SemanticFact).where(
            SemanticFact.owner_id == owner_id,
            effective_fact_condition(),
        )
        if subject_kind in _VALID_SUBJECT_KINDS:
            base = base.where(SemanticFact.subject_kind == subject_kind)
        if agent_id:
            base = base.where(SemanticFact.agent_id == agent_id)
        scope_cond = scope_visibility_condition(scope_kind=scope_kind, scope_id=scope_id, project_id=project_id)
        if scope_cond is not None:
            base = base.where(scope_cond)
        total = (await db.execute(sa.select(sa.func.count()).select_from(base.subquery()))).scalar_one()
        rows = (
            (
                await db.execute(
                    base.order_by(*_project_first_order(project_id), SemanticFact.updated_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return {'total': total, 'items': [_serialize(r) for r in rows], 'limit': limit, 'offset': offset}


semantic_fact_service = SemanticFactService()
