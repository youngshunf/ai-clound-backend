"""Phase 7: Permission Engine —— 中央四态判决入口 (A 路线，设计 01 §3)。

evaluate(db, sender, receiver, envelope) → DecisionResult
- 优先调 iron_laws.check_iron_laws；命中即返回
- iron_laws None → 灰度委托 route_guard.check_permission (仅 diff log，不阻断；灰度期 1 周后评估退役)
- 矩阵层默认 ALLOW (细粒度矩阵留给 spawner 工具层 phase)
- 任何路径都 _audit_safe(...) 写一条 permission_decision audit (失败仅 log.warning)
- Fail-closed 在 evaluate 顶层 try/except 内：内部异常 → DENY error_code=2099 (D-03)

下游契约：返回的 DecisionResult.decision 字面量必须是 'allow' / 'deny' /
'confirm_required' / 'scope_limited'，与 07-01 hasn_core::PermissionDecision
serde rename_all='snake_case' 字节对齐 (router 投递 envelope 时直接写入)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from backend.app.hasn.constants import ALLOW, DENY, get_default_permissions
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_contacts import HasnContacts
from backend.app.hasn.service.hasn_audit_log_service import hasn_audit_log_service
from backend.app.hasn.service.iron_laws import DecisionResult, check_iron_laws
from backend.app.hasn.service.route_guard import route_guard
from backend.common.log import log


class PermissionEngine:
    """中央四态判决器 (单例，模块级 permission_engine 暴露)。"""

    async def evaluate(
        self,
        db: Any,  # AsyncSession
        *,
        sender: dict[str, Any],
        receiver: dict[str, Any],
        envelope: dict[str, Any],
    ) -> DecisionResult:
        """中央判决入口。Fail-closed on internal exception (D-03)。"""
        try:
            # 1. 七铁律优先
            iron = await check_iron_laws(db, sender, receiver, envelope)
            if iron is not None:
                await self._audit_safe(db, sender, receiver, iron)
                return iron

            # 2. 矩阵层 H2H 关系门控（C1 安全修复）：人→人普通消息不经入站门控
            #    （inbound_gatekeeper 只管 to=Agent），历史在此默认 ALLOW → 陌生人可直发，
            #    违反 Core/04 §1 发布门禁。这里对 H2H 调 route_guard 关系判定（无关系 / 黑名单
            #    → DENY）。涉及 Agent / service / system 的消息不在此拦截：to=Agent 已由
            #    inbound_gatekeeper 五闸门控；service→Agent / Agent→主人 等各有链路。
            sender_id = sender.get('hasn_id', '')
            receiver_id = receiver.get('hasn_id', '')
            if (
                sender.get('entity_type') == 'human'
                and receiver.get('entity_type') == 'human'
                and sender_id
                and sender_id != receiver_id
            ):
                try:
                    h2h_ok = await route_guard.check_permission(
                        db, sender_id, receiver_id,
                        envelope.get('relation_type', 'social'),
                    )
                except Exception as exc:
                    log.warning(f'[perm_engine] H2H route_guard 异常, fail-closed DENY: {exc}')
                    h2h_ok = False
                if not h2h_ok:
                    deny = DecisionResult(
                        decision=DENY,
                        reason='no relationship: stranger H2H message blocked (Core/04 §1)',
                        matched_rule='matrix_h2h_relation',
                        error_code=2002,
                    )
                    await self._audit_safe(db, sender, receiver, deny, severity='warning')
                    return deny

            # 2.5 A2H 出站关系门（doc07 §8-1 补洞）：分身主动发人类时，历史上跳过关系门、
            #     过完铁律即 ALLOW —— 分身能主动够到任意陌生人类，出站侧无任何关系/信任差异化拦截
            #     （差异化本在对端收方入站闸，但对端是人类时没有入站闸）。这里对「sender=agent 且
            #     receiver=human 且 receiver ≠ 该分身所属 owner」查发送方 owner 与 receiver 的关系矩阵。
            #     A→自己主人（owner loopback，本不经云端）豁免；群消息另有群成员校验，不在此拦截。
            if (
                sender.get('entity_type') == 'agent'
                and receiver.get('entity_type') == 'human'
                and receiver_id
            ):
                a2h_deny = await self._a2h_outbound_gate(
                    db, sender_id=sender_id, receiver_id=receiver_id, envelope=envelope,
                )
                if a2h_deny is not None:
                    await self._audit_safe(db, sender, receiver, a2h_deny, severity='warning')
                    return a2h_deny

            # 3. 矩阵放行（H2H 已过关系门控；细粒度 action 矩阵留工具层 phase）
            result = DecisionResult(
                decision=ALLOW,
                reason='matrix passed',
                matched_rule='matrix',
            )
            await self._audit_safe(db, sender, receiver, result)
            return result

        except Exception as exc:
            log.exception(
                f'[perm_engine] evaluate 内部异常, Fail-closed DENY: {exc}'
            )
            fail = DecisionResult(
                decision=DENY,
                reason='internal error, fail-closed',
                matched_rule='exception',
                error_code=2099,
            )
            await self._audit_safe(db, sender, receiver, fail, severity='warning')
            return fail

    async def _a2h_outbound_gate(
        self,
        db: Any,
        *,
        sender_id: str,
        receiver_id: str,
        envelope: dict[str, Any],
    ) -> DecisionResult | None:
        """分身 → 人类出站关系门（doc07 §8-1）。

        返回 DecisionResult(DENY) 表示拦截；返回 None 表示放行（豁免或矩阵允许）。
        - 发送方分身所属 owner 即接收方本人（A→自己主人）→ 豁免（None）。
        - 复用 owner↔receiver 联系人镜像 + `get_default_permissions` 判 `send_message`：
          Blocked（status=blocked 或 trust_level=0）直接 DENY；无联系人按陌生人（trust=1）走矩阵默认。
        - fail-closed：解析异常按 DENY 处置（与 H2H 关系门一致，宁可误拦不误发）。
        """
        try:
            agent = await db.execute(
                select(HasnAgents).where(HasnAgents.hasn_id == sender_id)
            )
            agent = agent.scalar_one_or_none()
            # 分身不存在（并发删除等）→ 交后续兜底，不在此拦（None）
            if agent is None:
                return None
            owner_id = agent.owner_id or ''
            # 纵深豁免：发给自己主人（owner loopback 本不经云端，双保险）
            if owner_id and receiver_id == owner_id:
                return None
            # owner 未知 → 无法判关系，fail-closed
            if not owner_id:
                return DecisionResult(
                    decision=DENY,
                    reason='a2h outbound: agent owner unknown, fail-closed',
                    matched_rule='matrix_a2h_relation',
                    error_code=2002,
                )

            # owner 视角下对 receiver 的联系人镜像（决定信任等级/黑名单）
            contact = (await db.execute(
                select(HasnContacts).where(
                    HasnContacts.owner_id == owner_id,
                    HasnContacts.peer_id == receiver_id,
                )
            )).scalar_one_or_none()
            eff_relation = (
                (contact.relation_type if contact else None)
                or envelope.get('relation_type')
                or 'social'
            )
            # 无联系人 = 陌生人（trust_level=1）；有则取其信任等级
            trust_level = (
                contact.trust_level
                if contact and contact.trust_level is not None
                else 1
            )
            # Blocked → 直接 DENY
            if contact is not None and (contact.status == 'blocked' or trust_level == 0):
                return DecisionResult(
                    decision=DENY,
                    reason='a2h outbound blocked: agent owner blocked this human',
                    matched_rule='matrix_a2h_relation',
                    error_code=2002,
                )
            # 矩阵 send_message 决策（陌生人/低信任 social 默认 DENY）
            perms = get_default_permissions(eff_relation, trust_level)
            if perms.get('send_message', DENY) == DENY:
                return DecisionResult(
                    decision=DENY,
                    reason=(
                        'a2h outbound: no relationship / trust too low '
                        f'(relation={eff_relation}, trust={trust_level})'
                    ),
                    matched_rule='matrix_a2h_relation',
                    error_code=2002,
                )
            return None
        except Exception as exc:  # fail-closed：解析异常宁可误拦不误发
            log.warning(f'[perm_engine] A2H 出站关系门异常, fail-closed DENY: {exc}')
            return DecisionResult(
                decision=DENY,
                reason='a2h outbound gate error, fail-closed',
                matched_rule='matrix_a2h_relation',
                error_code=2002,
            )

    async def _audit_safe(
        self,
        db: Any,
        sender: dict[str, Any],
        receiver: dict[str, Any],
        result: DecisionResult,
        severity: str | None = None,
    ) -> None:
        """审计写入。失败仅 log.warning，不 raise (业务可用性优先 / L8)。"""
        try:
            await hasn_audit_log_service.append(
                db=db,
                actor_id=sender.get('hasn_id') or 'central',
                actor_type=sender.get('entity_type', 'system'),
                action='permission_decision',
                target_id=receiver.get('hasn_id'),
                target_type=receiver.get('entity_type', 'agent'),
                details={
                    'decision': result.decision,
                    'reason': result.reason,
                    'matched_rule': result.matched_rule,
                    'allowed_fields': result.allowed_fields,
                    'from': sender.get('hasn_id'),
                    'to': receiver.get('hasn_id'),
                },
                severity=severity or (
                    'warning' if result.decision == DENY else None
                ),
            )
        except Exception as exc:
            log.warning(f'[perm_engine] audit append 失败 (不阻断): {exc}')


# 模块级单例 (与 route_guard / hasn_audit_log_service 保持同风格)
permission_engine: PermissionEngine = PermissionEngine()
