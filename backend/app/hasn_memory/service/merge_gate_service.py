"""云端合并闸（doc19 §5.5 / §5.6 · D-3 / D-4 / D-17 / D-18）。

设计事实源：``docs/hasn-node设计文档/02-记忆与知识库/19-多节点记忆分层与分身自治整理设计.md``

合并本身在**主脑分身自己的设备上**跑（§5.1，云端 LLM 合并已整条退役）。云端只保留一个
**权威串行点**：整轮结果提交上来时校验「你是不是当前主脑、你的基线对不对」，然后原子落库并
推进回灌游标。**云端不做任何语义判断**（§8.5）。

为什么必须有这道闸（§5.6 / D-18）：主脑换绑设备的传播窗口内，两台设备会各自自认主脑；本地
互斥防不住这一幕。旧主脑的迟到提交在这里整轮被拒（warn，下轮重跑），无双写风险。

六步校验，全部在同一事务内，任一不符**整轮拒绝**：

1. ``pg_advisory_xact_lock('memory-merge:{owner}')``——同 owner 串行（先例：sync_events revision lock）；
2. **主脑校验**：提交分身必须是该 owner 当前主脑，且 ``node_id`` 是它当前绑定的节点 → 否则
   409 ``not_master_brain``；
3. **run_id 幂等**：同 run_id 且已 applied → 只读重算一份等价响应返回，**不重复落库**；
4. **CAS**：``base_owner_memory_version`` 必须等于库中 ``owner_memory.version`` → 否则
   409 ``version_conflict``；
5. **逐条失效护栏**（§3.4 核心）：``judged_revision`` 与库中当前 ``revision`` 不等的裁决
   **跳过该条**（事实已被本地整理或主人改过，裁决过期），计入 ``skipped_verdicts``——
   **不是整轮失败**；
6. **整轮替换**（D-4）：本轮 ``run_id`` 之外的旧 overlay 全部清空，上一轮裁决整体作废。
   这是「上一轮错误裁决下一轮自动纠正」成立的前提，**不是**叠加。

⚠️ 校验顺序上，run_id 幂等（3）刻意排在 CAS（4）**之前**：一轮成功 apply 后
``owner_memory.version`` 已经 +1，主脑网络抖动重发同一 run 时基线必然对不上，若先做 CAS
就会把「幂等重放」误判成 ``version_conflict``，主脑于是重跑一轮完全相同的合并——幂等键就白设了。

拒绝路径**必须留痕**（§5.5「不接受静默」）：``merge_run(status='rejected', reject_reason)``
写在**独立事务**里，因为请求事务连同 advisory lock 会随异常整体回滚——留痕若跟着回滚，主人在
记忆页就只看得到「很久没整理了」，看不到「为什么没整理成」。
"""

from __future__ import annotations

import json

from datetime import timedelta
from time import time
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_memory.model import HasnOwnerMemory
from backend.app.hasn_memory.schema.merge_gate import (
    MergeApplyRequest,
    MergeApplyResponse,
    MergeDerivedFactItem,
    MergeRequestBody,
    MergeRequestResponse,
    MergeStatusResponse,
    PendingMergeRequest,
    SkippedVerdict,
)
from backend.app.hasn_memory.service.fact_uplink_service import fact_uplink_service
from backend.app.hasn_memory.service.merge_request_service import merge_request_service
from backend.app.hasn_memory.service.merge_run_service import merge_run_service
from backend.app.hasn_memory.service.owner_memory_service import _ensure_identity_lines, _estimate_tokens
from backend.app.hasn_memory.service.peer_portrait_service import peer_portrait_service
from backend.app.hasn_memory.service.transaction_lock import acquire_memory_transaction_lock
from backend.app.home.service.workbench_pref_service import resolve_master_brain_agent_id
from backend.common.exception import errors
from backend.common.log import log
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: §5.5 建议阈值：超过这么久没成功合并就在主人主页给一条提示（如实告知，不是 error）。
STALE_THRESHOLD_DAYS = 7

#: 拒绝原因枚举（与 `merge_run.reject_reason` 落库值一致，主脑据此决定下轮怎么重跑）。
REJECT_NOT_MASTER_BRAIN = 'not_master_brain'
REJECT_VERSION_CONFLICT = 'version_conflict'
REJECT_RUN_ID_OWNER_MISMATCH = 'run_id_owner_mismatch'

_SECONDS_PER_DAY = 86400.0


def _now_ms() -> int:
    return int(time() * 1000)


def _object_json(value: Any) -> str:
    """归一化 ``object_json``：永远存合法 JSON 串（与 semantic_fact_service 同口径）。"""
    if isinstance(value, str):
        try:
            json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return json.dumps(value, ensure_ascii=False)
        return value
    return json.dumps(value, ensure_ascii=False)


class MergeGateRejectedError(Exception):
    """整轮拒绝（内部信号）。端点层转 409 + ``rejected_reason``。"""

    def __init__(self, reason: str, message: str, *, detail: str | None = None) -> None:
        self.reason = reason
        self.message = message
        self.detail = detail
        super().__init__(message)


class MergeGateService:
    """合并闸：整轮原子 apply + 合并待办 + 主脑单点可见性。"""

    # ------------------------------------------------------------------ apply

    async def apply(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        agent_id: str,
        body: MergeApplyRequest,
    ) -> MergeApplyResponse:
        """应用整轮合并结果。校验不过整轮拒绝（抛 `MergeGateRejectedError`，已在独立事务留痕）。"""
        # 1) owner advisory lock：同 owner 的合并串行，防主脑换绑窗口的双提交交叉写。
        await acquire_memory_transaction_lock(db, f'memory-merge:{owner_id}')

        # 2) 主脑校验（D-18 的权威互斥；本地互斥只是优化）。
        await self._assert_master_brain(db, owner_id=owner_id, agent_id=agent_id, body=body)

        # 3) run_id 幂等：已 applied 的同一轮直接返回等价结果，绝不重复应用。
        existing_run = await merge_run_service.get(db, body.run_id)
        if existing_run is not None and existing_run.owner_id != owner_id:
            # run_id 撞到别人的轮次：不是并发，是身份错乱，必须拒绝而不是覆盖。
            await self._record_rejected(owner_id=owner_id, agent_id=agent_id, body=body,
                                        reason=REJECT_RUN_ID_OWNER_MISMATCH)
            raise MergeGateRejectedError(REJECT_RUN_ID_OWNER_MISMATCH, 'run_id 已属于其他主人的合并轮次')
        if existing_run is not None and existing_run.status == 'applied':
            log.warning(f'合并闸幂等重放：owner={owner_id} run={body.run_id}（不重复应用）')
            return await self._replay_response(db, owner_id=owner_id, body=body)

        # 4) CAS：基线必须与库中当前版本一致（§5.6）。
        current_version = await self._owner_memory_version(db, owner_id)
        if int(body.base_owner_memory_version) != current_version:
            await self._record_rejected(owner_id=owner_id, agent_id=agent_id, body=body,
                                        reason=REJECT_VERSION_CONFLICT)
            log.warning(
                f'合并闸拒绝 version_conflict：owner={owner_id} run={body.run_id} '
                f'base={body.base_owner_memory_version} current={current_version}'
            )
            raise MergeGateRejectedError(REJECT_VERSION_CONFLICT, 'owner_memory 基线版本与云端不一致，请重跑本轮合并')

        touched_fact_ids: list[str] = []

        # 5+6) 先整轮清空旧 overlay（D-4：上一轮整体作废，不是叠加），再落本轮裁决。
        touched_fact_ids.extend(await self._clear_stale_overlay(db, owner_id=owner_id, run_id=body.run_id))
        applied_ids, skipped = await self._apply_verdicts(db, owner_id=owner_id, body=body)
        touched_fact_ids.extend(applied_ids)

        derived_created, derived_ids = await self._apply_derived_facts(db, owner_id=owner_id, body=body)
        touched_fact_ids.extend(derived_ids)

        new_version = await self._apply_owner_memory(db, owner_id=owner_id, agent_id=agent_id, body=body,
                                                     base_version=current_version)
        portraits_updated = await self._apply_portraits(db, owner_id=owner_id, agent_id=agent_id, body=body)

        # 回灌广播：overlay 变更（含被清空的旧裁决）与派生事实都要发，否则各节点看到的
        # 生效可见性与云端不一致——「本机查不到、别的设备查得到」正是要消灭的困惑。
        for fact_id in dict.fromkeys(touched_fact_ids):
            await fact_uplink_service.emit_fact_downlink(db, owner_id=owner_id, fact_id=fact_id)

        await self._upsert_run(
            db,
            owner_id=owner_id,
            agent_id=agent_id,
            body=body,
            status='applied',
            reject_reason=None,
        )
        # 本轮已把待办消化掉（主脑上线后由 task_scheduler 触发合并时顺带消化，§5.5）。
        await merge_request_service.consume(db, owner_id)
        await db.flush()

        log.info(
            f'合并闸已应用：owner={owner_id} run={body.run_id} node={body.node_id} '
            f'version={new_version} verdicts={len(applied_ids)} skipped={len(skipped)} '
            f'derived={derived_created} portraits={portraits_updated}'
        )
        return MergeApplyResponse(
            applied=True,
            run_id=body.run_id,
            new_owner_memory_version=new_version,
            skipped_verdicts=skipped,
            derived_created=derived_created,
            portraits_updated=portraits_updated,
            replayed=False,
        )

    # ------------------------------------------------------------- 校验与幂等

    async def _assert_master_brain(
        self, db: AsyncSession, *, owner_id: str, agent_id: str, body: MergeApplyRequest
    ) -> None:
        """提交者必须是当前主脑，且 ``node_id`` 是它当前绑定的节点（§4.4 / §5.6）。"""
        master_id = await resolve_master_brain_agent_id(db, owner_id)
        if not master_id or master_id != agent_id:
            await self._record_rejected(owner_id=owner_id, agent_id=agent_id, body=body,
                                        reason=REJECT_NOT_MASTER_BRAIN)
            log.warning(
                f'合并闸拒绝 not_master_brain：owner={owner_id} submitter={agent_id} current_master={master_id}'
            )
            raise MergeGateRejectedError(REJECT_NOT_MASTER_BRAIN, '只有当前主脑分身可以提交合并结果')

        row = (
            await db.execute(
                sa.select(HasnAgents.binding_node_id, HasnAgents.node_id).where(HasnAgents.hasn_id == agent_id).limit(1)
            )
        ).first()
        bound_nodes = {n for n in (row or (None, None)) if n}
        if body.node_id not in bound_nodes:
            # 主脑换绑窗口：分身还是主脑，但提交来自它已经离开的那台设备（或该设备从未上报绑定）。
            # 放行等于承认一个不知在哪儿跑的合并，故一律拒——这正是本闸兜底的那一幕。
            await self._record_rejected(owner_id=owner_id, agent_id=agent_id, body=body,
                                        reason=REJECT_NOT_MASTER_BRAIN)
            log.warning(
                f'合并闸拒绝 not_master_brain（节点不符）：owner={owner_id} agent={agent_id} '
                f'submitted_node={body.node_id} bound_nodes={sorted(bound_nodes)}'
            )
            raise MergeGateRejectedError(
                REJECT_NOT_MASTER_BRAIN, '提交节点不是该主脑分身当前绑定的节点', detail='node_mismatch'
            )

    async def _owner_memory_version(self, db: AsyncSession, owner_id: str) -> int:
        """库中当前 ``owner_memory.version``；无行时基线为 0（首轮合并的合法基线）。"""
        version = (
            await db.execute(sa.select(HasnOwnerMemory.version).where(HasnOwnerMemory.owner_id == owner_id).limit(1))
        ).scalar_one_or_none()
        return int(version or 0)

    async def _replay_response(
        self, db: AsyncSession, *, owner_id: str, body: MergeApplyRequest
    ) -> MergeApplyResponse:
        """同一 run_id 的幂等重放响应：**全部字段现查现算，一个都不编**。

        ``derived_created`` / ``portraits_updated`` 记的是「本次调用新建了几条」——重放没有新建，
        故为 0（不是「上轮建了几条」，那会让调用方以为又写了一遍）。``skipped_verdicts`` 按当前
        库状态重新判一遍：重放时事实又被改过的话，它本来也不该再被算作已应用。
        """
        skipped: list[SkippedVerdict] = []
        for item in body.verdicts:
            current = await self._fact_revision(db, owner_id=owner_id, fact_id=item.fact_id)
            if current is None:
                skipped.append(
                    SkippedVerdict(fact_id=item.fact_id, reason='fact_not_found',
                                   judged_revision=item.judged_revision, current_revision=None)
                )
            elif current != item.judged_revision:
                skipped.append(
                    SkippedVerdict(fact_id=item.fact_id, reason='verdict_stale',
                                   judged_revision=item.judged_revision, current_revision=current)
                )
        return MergeApplyResponse(
            applied=True,
            run_id=body.run_id,
            new_owner_memory_version=await self._owner_memory_version(db, owner_id),
            skipped_verdicts=skipped,
            derived_created=0,
            portraits_updated=0,
            replayed=True,
        )

    async def _fact_revision(self, db: AsyncSession, *, owner_id: str, fact_id: str) -> int | None:
        row = (
            await db.execute(
                sa.text(
                    'SELECT revision FROM hasn_memory.semantic_fact WHERE fact_id = :fact_id AND owner_id = :owner_id'
                ),
                {'fact_id': fact_id, 'owner_id': owner_id},
            )
        ).scalar_one_or_none()
        return int(row) if row is not None else None

    # ------------------------------------------------------------ overlay 应用

    async def _clear_stale_overlay(self, db: AsyncSession, *, owner_id: str, run_id: str) -> list[str]:
        """清空**非本轮**的旧 overlay（D-4 整轮替换语义）。返回被清空的 fact_id 清单。

        只碰 overlay 三列，绝不触业务字段组、绝不推进 revision（§3.4 单一写者）。
        """
        rows = (
            await db.execute(
                sa.text(
                    """
                    UPDATE hasn_memory.semantic_fact
                       SET merge_verdict = NULL,
                           merge_verdict_run = NULL,
                           merge_judged_revision = NULL
                     WHERE owner_id = :owner_id
                       AND merge_verdict_run IS DISTINCT FROM :run_id
                       AND (merge_verdict IS NOT NULL OR merge_verdict_run IS NOT NULL
                            OR merge_judged_revision IS NOT NULL)
                    RETURNING fact_id
                    """
                ),
                {'owner_id': owner_id, 'run_id': run_id},
            )
        ).scalars().all()
        return [str(r) for r in rows]

    async def _apply_verdicts(
        self, db: AsyncSession, *, owner_id: str, body: MergeApplyRequest
    ) -> tuple[list[str], list[SkippedVerdict]]:
        """逐条落 overlay，带失效护栏（§3.4 / §5.6）。过期的裁决**跳过该条**，不拖垮整轮。"""
        applied: list[str] = []
        skipped: list[SkippedVerdict] = []
        for item in body.verdicts:
            current = await self._fact_revision(db, owner_id=owner_id, fact_id=item.fact_id)
            if current is None:
                # 事实尚未汇聚到云端，或已被主人 purge：裁决无处可落，留待下轮。
                skipped.append(
                    SkippedVerdict(fact_id=item.fact_id, reason='fact_not_found',
                                   judged_revision=item.judged_revision, current_revision=None)
                )
                continue
            if current != item.judged_revision:
                # 裁决作出后事实又被本地整理 / 主人改过 → 该裁决过期作废（竞态靠过期消解，不靠锁）。
                skipped.append(
                    SkippedVerdict(fact_id=item.fact_id, reason='verdict_stale',
                                   judged_revision=item.judged_revision, current_revision=current)
                )
                continue
            await db.execute(
                sa.text(
                    """
                    UPDATE hasn_memory.semantic_fact
                       SET merge_verdict = :verdict,
                           merge_verdict_run = :run_id,
                           merge_judged_revision = :judged_revision
                     WHERE fact_id = :fact_id AND owner_id = :owner_id
                    """
                ),
                {
                    'verdict': item.verdict,
                    'run_id': body.run_id,
                    'judged_revision': item.judged_revision,
                    'fact_id': item.fact_id,
                    'owner_id': owner_id,
                },
            )
            applied.append(item.fact_id)
        if skipped:
            log.warning(
                f'合并闸跳过 {len(skipped)} 条过期裁决（留待下轮重裁）：owner={owner_id} run={body.run_id} '
                f'facts={[s.fact_id for s in skipped]}'
            )
        await db.flush()
        return applied, skipped

    # ---------------------------------------------------------- 派生事实应用

    async def _apply_derived_facts(
        self, db: AsyncSession, *, owner_id: str, body: MergeApplyRequest
    ) -> tuple[int, list[str]]:
        """落 ``origin_kind='merged'`` 派生事实（§3.2）。返回 (新建条数, 全部 fact_id)。

        派生事实只由合并维护，任何分身不得直接整理（§4.3），故 ``origin_node_id`` /
        ``origin_agent_id`` 一律留空——它们不属于任何节点的自产片。
        """
        if not body.derived_facts:
            return 0, []
        fact_ids = [item.fact_id for item in body.derived_facts]
        existing = set(
            (
                await db.execute(
                    sa
                    .text(
                        'SELECT fact_id FROM hasn_memory.semantic_fact '
                        'WHERE owner_id = :owner_id AND fact_id IN :ids'
                    )
                    .bindparams(sa.bindparam('ids', expanding=True)),
                    {'owner_id': owner_id, 'ids': fact_ids},
                )
            )
            .scalars()
            .all()
        )
        now = _now_ms()
        for item in body.derived_facts:
            agent_id, subject_id, scope_kind, scope_id = _normalize_derived_subject(item, owner_id=owner_id)
            await db.execute(
                sa.text(
                    """
                    INSERT INTO hasn_memory.semantic_fact (
                        fact_id, owner_id, agent_id, subject_kind, subject_id, memory_layer,
                        scope_kind, scope_id, predicate, object_json, confidence, status,
                        source_turn_ids, source_refs_json, rationale, created_at, updated_at,
                        origin_kind, origin_node_id, origin_agent_id, merged_from, revision
                    ) VALUES (
                        :fact_id, :owner_id, :agent_id, :subject_kind, :subject_id, 'semantic',
                        :scope_kind, :scope_id, :predicate, :object_json, :confidence, 'active',
                        '[]', '[]', :rationale, :now, :now,
                        'merged', NULL, NULL, :merged_from, 1
                    )
                    ON CONFLICT (fact_id) DO UPDATE SET
                        subject_kind = EXCLUDED.subject_kind,
                        subject_id = EXCLUDED.subject_id,
                        agent_id = EXCLUDED.agent_id,
                        scope_kind = EXCLUDED.scope_kind,
                        scope_id = EXCLUDED.scope_id,
                        predicate = EXCLUDED.predicate,
                        object_json = EXCLUDED.object_json,
                        confidence = EXCLUDED.confidence,
                        status = 'active',
                        rationale = EXCLUDED.rationale,
                        merged_from = EXCLUDED.merged_from,
                        origin_kind = 'merged',
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    'fact_id': item.fact_id,
                    'owner_id': owner_id,
                    'agent_id': agent_id,
                    'subject_kind': item.subject_kind,
                    'subject_id': subject_id,
                    'scope_kind': scope_kind,
                    'scope_id': scope_id,
                    'predicate': item.predicate.strip(),
                    'object_json': _object_json(item.object_json),
                    'confidence': float(item.confidence),
                    'rationale': item.rationale,
                    'now': now,
                    'merged_from': json.dumps(list(item.merged_from), ensure_ascii=False),
                },
            )
        await db.flush()
        return len([fid for fid in fact_ids if fid not in existing]), fact_ids

    # ----------------------------------------------------------- 画像与 USER.md

    async def _apply_owner_memory(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        agent_id: str,
        body: MergeApplyRequest,
        base_version: int,
    ) -> int:
        """写合并态 USER.md 并 MEMPUSH 下发；返回新版本号。

        `version` **每轮成功 apply 都 +1**（§5.6「apply 成功后 owner_memory.version + 1」），
        正文没变也一样——它是轮次水位，下一轮的 CAS 基线就取它；用「正文变了才 +1」会让主脑
        在无变化轮之后拿着旧基线反复被判 version_conflict。
        """
        new_version = base_version + 1
        content = (body.owner_memory.content or '').strip() if body.owner_memory else ''
        now = timezone.now()
        values: dict[str, Any] = {
            'version': new_version,
            'last_merged_time': now,
            'last_merge_run_id': body.run_id,
            'last_merge_node_id': body.node_id,
            'last_merge_summary': body.summary,
        }
        if content:
            nickname = (
                await db.execute(sa.select(HasnHumans.nickname).where(HasnHumans.hasn_id == owner_id).limit(1))
            ).scalar_one_or_none()
            content = _ensure_identity_lines(content, nickname=nickname or '', owner_id=owner_id)
            values['content'] = content
            values['token_count'] = _estimate_tokens(content)
            # 主人手工版本已被本轮重算消费（§4.6 要求 prompt 携带并保留其意图），标位复位。
            values['owner_edited'] = False

        # 本轮无正文时只推进版本与 last_merge_*，**不覆盖已有正文**（插入分支才落 content=None）。
        insert_values: dict[str, Any] = {'owner_id': owner_id, 'content': None}
        insert_values.update(values)
        await db.execute(
            pg_insert(HasnOwnerMemory)
            .values(**insert_values)
            .on_conflict_do_update(index_elements=['owner_id'], set_=values)
        )
        await db.flush()

        if content:
            # MEMPUSH（doc19 §10 保留）：覆盖该 owner 全部分身的 user_md 并 bump profile_revision，
            # Runtime 轮询 revision 变化后重新拉取覆盖本地 USER.md。
            await db.execute(
                sa
                .update(HasnAgents)
                .where(HasnAgents.owner_id == owner_id)
                .values(user_md=content, profile_revision=HasnAgents.profile_revision + 1)
            )
            try:
                from backend.app.hasn.service.sync_invalidate_service import KIND_AGENTS, bump_owner

                await bump_owner(KIND_AGENTS, db, owner_id)
            except Exception as exc:  # 推送 best-effort：权威已落库，推送失败靠重连握手追平
                log.warning(f'合并闸 WSPUSH agents invalidate 失败 owner={owner_id} agent={agent_id}: {exc}')
        return new_version

    async def _apply_portraits(
        self, db: AsyncSession, *, owner_id: str, agent_id: str, body: MergeApplyRequest
    ) -> int:
        """落主脑重算好的 peer 画像并发下行；返回写入条数。"""
        updated = 0
        for item in body.peer_portraits:
            await peer_portrait_service.upsert_merged_portrait(
                db,
                owner_id=owner_id,
                peer_hasn_id=item.peer_hasn_id,
                portrait_text=item.portrait_text,
                peer_kind=item.peer_kind,
                revised_by=agent_id,
            )
            updated += 1
        return updated

    # ------------------------------------------------------------- 轮次留痕

    async def _upsert_run(
        self,
        db: AsyncSession,
        *,
        owner_id: str,
        agent_id: str,
        body: MergeApplyRequest,
        status: str,
        reject_reason: str | None,
    ) -> None:
        """登记 / 覆盖一轮合并结果（同一 run_id 先被拒后重跑成功时覆盖为 applied）。"""
        await merge_run_service.upsert_run(
            db,
            run_id=body.run_id,
            owner_id=owner_id,
            submitted_node_id=body.node_id,
            submitted_agent_id=agent_id,
            base_owner_memory_version=int(body.base_owner_memory_version),
            status=status,
            reject_reason=reject_reason,
            facts_judged=int(body.stats.facts_judged),
            facts_merged=int(body.stats.facts_merged),
            facts_disputed=int(body.stats.facts_disputed),
            summary=body.summary,
        )

    async def _record_rejected(
        self, *, owner_id: str, agent_id: str, body: MergeApplyRequest, reason: str
    ) -> None:
        """在**独立事务**里登记被拒轮次（§5.5 主人可见性的数据源）。

        请求事务连同 advisory lock 会随异常整体回滚，留痕若跟着回滚，主人在记忆页只会看到
        「很久没整理了」而看不到「为什么没整理成」——静默停摆正是本节明确不接受的。
        本身失败不改变拒绝结论，只 warn（拒绝已经发生，留痕失败不能反过来放行）。
        """
        try:
            async with async_db_session.begin() as fresh:
                await merge_run_service.upsert_run(
                    fresh,
                    run_id=body.run_id,
                    owner_id=owner_id,
                    submitted_node_id=body.node_id,
                    submitted_agent_id=agent_id,
                    base_owner_memory_version=int(body.base_owner_memory_version),
                    status='rejected',
                    reject_reason=reason,
                    facts_judged=int(body.stats.facts_judged),
                    facts_merged=int(body.stats.facts_merged),
                    facts_disputed=int(body.stats.facts_disputed),
                    summary=body.summary,
                    owner_guard=owner_id,
                )
        except Exception as exc:
            log.warning(f'合并闸拒绝留痕失败（不改变拒绝结论）owner={owner_id} run={body.run_id}: {exc}')

    # ------------------------------------------------------------- 合并待办

    async def request_merge(
        self, db: AsyncSession, *, owner_id: str, agent_id: str, body: MergeRequestBody
    ) -> MergeRequestResponse:
        """非主脑分身请求合并（§5.5）：每 owner 一条待办，重复请求覆盖，**不堆积**。"""
        master_id = await resolve_master_brain_agent_id(db, owner_id)
        row = await merge_request_service.request_merge(
            db,
            owner_id=owner_id,
            requested_by_agent=agent_id,
            requested_by_node=body.node_id,
            reason=body.reason,
        )
        await db.flush()
        return MergeRequestResponse(
            accepted=True,
            is_master_brain=bool(master_id and master_id == agent_id),
            pending=_pending_schema(row),
        )

    # --------------------------------------------------------- 主脑单点可见性

    async def merge_status(self, db: AsyncSession, *, owner_id: str) -> MergeStatusResponse:
        """§5.5：上次整理于 X、主脑在哪台设备、当前是否离线、是否有待办、是否超阈值。"""
        last_applied = await merge_run_service.latest_applied(db, owner_id)
        pending = await merge_request_service.get_pending(db, owner_id)
        now = timezone.now()

        days_since: float | None = None
        if last_applied is not None:
            reference = last_applied.finished_time or last_applied.started_time
            if reference is not None:
                days_since = max(0.0, (now - reference).total_seconds() / _SECONDS_PER_DAY)

        master_id = await resolve_master_brain_agent_id(db, owner_id)
        master_node_id: str | None = None
        if master_id:
            row = (
                await db.execute(
                    sa
                    .select(HasnAgents.binding_node_id, HasnAgents.node_id)
                    .where(HasnAgents.hasn_id == master_id)
                    .limit(1)
                )
            ).first()
            if row is not None:
                master_node_id = row[0] or row[1]

        last_rejected = await merge_run_service.latest_rejected(db, owner_id)
        node_names = await self._node_names(db, [last_applied.submitted_node_id if last_applied else None,
                                                 master_node_id])
        return MergeStatusResponse(
            owner_memory_version=await self._owner_memory_version(db, owner_id),
            last_merge_run_id=last_applied.run_id if last_applied else None,
            last_merge_time=(last_applied.finished_time or last_applied.started_time) if last_applied else None,
            last_merge_node_id=last_applied.submitted_node_id if last_applied else None,
            last_merge_node_name=node_names.get(last_applied.submitted_node_id) if last_applied else None,
            last_merge_agent_id=last_applied.submitted_agent_id if last_applied else None,
            last_merge_summary=last_applied.summary if last_applied else None,
            days_since_last_merge=days_since,
            master_brain_agent_id=master_id,
            master_brain_node_id=master_node_id,
            master_brain_node_name=node_names.get(master_node_id) if master_node_id else None,
            master_brain_online=await self._node_online(master_node_id),
            has_pending_request=pending is not None,
            pending_request=_pending_schema(pending, now=now) if pending is not None else None,
            last_rejected_run_id=last_rejected.run_id if last_rejected else None,
            last_rejected_reason=last_rejected.reject_reason if last_rejected else None,
            last_rejected_time=(last_rejected.finished_time or last_rejected.started_time) if last_rejected else None,
            stale_over_threshold=_is_stale(days_since=days_since, pending=pending, now=now),
            stale_threshold_days=STALE_THRESHOLD_DAYS,
        )

    async def _node_names(self, db: AsyncSession, node_ids: list[str | None]) -> dict[str, str | None]:
        wanted = [n for n in node_ids if n]
        if not wanted:
            return {}
        from backend.app.hasn.model.hasn_nodes import HasnNodes

        rows = (
            await db.execute(sa.select(HasnNodes.node_id, HasnNodes.node_name).where(HasnNodes.node_id.in_(wanted)))
        ).all()
        return {str(r[0]): r[1] for r in rows}

    async def _node_online(self, node_id: str | None) -> bool | None:
        """节点在线判定：复用 IM 侧既有 presence 源（心跳 TTL 键），**不另造在线口径**。

        节点未知或 presence 源不可用时返回 **None（判不了）**，绝不用「查不到就算离线」
        冒充确定结论——主人看到「当前离线」会据此去开那台设备，误报的代价是真实的。
        """
        if not node_id:
            return None
        try:
            from backend.app.hasn_im.application.provider import get_node_session_gateway

            return bool(await get_node_session_gateway().is_node_online(node_id))
        except Exception as exc:
            log.warning(f'合并状态：节点在线判定不可用 node={node_id}: {exc}')
            return None


def _normalize_derived_subject(
    item: MergeDerivedFactItem, *, owner_id: str
) -> tuple[str | None, str, str, str]:
    """把派生事实的主体/作用域规约到表 CHECK 允许的组合（与 `save_fact` 同口径）。

    返回 ``(agent_id, subject_id, scope_kind, scope_id)``。
    """
    subject_id = (item.subject_id or '').strip()
    if item.subject_kind == 'agent_self':
        if not subject_id:
            raise errors.RequestError(msg=f'派生事实 {item.fact_id} 缺少 agent_self 主体 ID')
        agent_id: str | None = subject_id
    else:
        agent_id = None
        if item.subject_kind == 'owner':
            subject_id = owner_id
        elif not subject_id:
            raise errors.RequestError(msg=f'派生事实 {item.fact_id} 缺少 {item.subject_kind} 主体 ID')
    scope_kind = item.scope_kind
    # 表 CHECK ck_semantic_fact_world_scope：world 主体不许用 global 作用域。
    if item.subject_kind == 'world' and scope_kind == 'global':
        scope_kind = 'topic'
    return agent_id, subject_id, scope_kind, (item.scope_id or subject_id or 'global')


def _pending_schema(row: Any, *, now: Any = None) -> PendingMergeRequest:
    reference = now or timezone.now()
    pending_days = max(0.0, (reference - row.requested_time).total_seconds() / _SECONDS_PER_DAY)
    return PendingMergeRequest(
        requested_time=row.requested_time,
        requested_by_agent=row.requested_by_agent,
        requested_by_node=row.requested_by_node,
        reason=row.reason,
        pending_days=pending_days,
    )


def _is_stale(*, days_since: float | None, pending: Any, now: Any) -> bool:
    """§5.5 阈值提示：超过 7 天未成功合并即置位（如实告知，不是 error）。

    从未合并过且**也没有待办**时返回 False——没有任何东西等着被整理，报「停摆」是噪音。
    从未合并但待办已滞留超阈值则置位：这正是「主脑设备长期关机」的典型形态。
    """
    if days_since is not None:
        return days_since >= STALE_THRESHOLD_DAYS
    if pending is None:
        return False
    return (now - pending.requested_time) >= timedelta(days=STALE_THRESHOLD_DAYS)


merge_gate_service: MergeGateService = MergeGateService()
