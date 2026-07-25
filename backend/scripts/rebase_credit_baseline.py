"""存量余额一次性 rebase（doc94 R1）。

**为什么必须有这一步**：P0 只是**冻结**了错误余额，没有**修正**它。云端曾经每小时把
「NewAPI 已用量 + 云端剩余额度」算成目标 quota 覆盖写回，所以 NewAPI 现在的钱包数字
不能直接当正确基线；而云端旧余额也早已不是权威。必须用**不可变商业凭证**重建一次基线。

**顺序要害**：本工具必须排在「账单中心切读 NewAPI」**之前**跑完。若先切读再 rebase，
中间窗口里用户看到的是被官方权威化的错误余额，并且能照这个错值真实消费。

## 基线怎么算

- **应得的永久额度** = 已支付且未退款的积分包 + 合法的 admin 赠送/注册赠送
  （全部取自不可变商业凭证：订单、退款单、履约事件；**不取任何余额表**——
  余额表正是被污染的那一侧）；
- **实际的永久消费** = NewAPI 请求日志里落在永久钱包上的消耗（由 NewAPI 汇总，
  云端不自行推断，也不持有 quota↔credit 换算常量）；
- **目标钱包余额** = 应得永久额度 − 实际永久消费；
- **订阅额度不参与 rebase**：订阅按 30 天周期重置，历史周期本来就该清零。
  当前周期的剩余额度由 N2 的周期对齐逻辑在合同激活时重建；把历史周期的剩余搬到今天
  等于凭空发额度。

## 三条安全约束

1. **默认 dry-run**：不加 ``--apply`` 只出报告，绝不写任何数据；
2. **算不出来就进人工清单**：资金来源无法判定、日志有缺口、或算出负数的账户
   一律进 ``manual_review``，**绝不静默归零，也绝不补成套餐全额**；
3. **写入走幂等履约事件**：以 ``admin:REBASE-{batch}-{user}:wallet-grant`` 为幂等键，
   通过 NewAPI 的幂等履约 API 做**增量**调整，不使用任何「设置绝对 quota」的入口——
   那正是本轮要消灭的反向数据流。同一批次重跑不会二次调整。

用法::

    # 只出报告（默认）
    uv run python backend/scripts/rebase_credit_baseline.py --batch 2026-07-25-A --out ./rebase-report

    # 维护窗内真正写入
    uv run python backend/scripts/rebase_credit_baseline.py --batch 2026-07-25-A --out ./rebase-report --apply

完成并核对后，请把本脚本、``NewApiCreditClient.get_consumption_summary`` 与 NewAPI 侧的
``/api/internal/v1/credit-consumption/{id}`` 入口一并删除（doc94 R1：迁移完成后必须删除入口）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text

from backend.app.billing.service.credit_grant_event_service import format_credits
from backend.app.newapi.credit_client import NewApiCreditError, newapi_credit_client
from backend.common.log import log
from backend.database.db import async_db_session

#: 支付订单状态：1=已支付。退款通过 fulfillment_status='reversed' 体现，不另判状态。
ORDER_PAID = 1

#: 人工复核原因（稳定枚举，便于报告分类统计）
REASON_CONSUMPTION_INDETERMINATE = 'consumption_indeterminate'
REASON_NEGATIVE_TARGET = 'negative_target'
REASON_ACCOUNT_UNREADABLE = 'account_unreadable'


@dataclass(slots=True)
class UserBaseline:
    """单个用户的目标基线与判定依据。"""

    user_id: int
    newapi_user_id: int
    entitled_wallet_credits: Decimal = Decimal(0)
    consumed_wallet_credits: Decimal = Decimal(0)
    target_wallet_credits: Decimal = Decimal(0)
    current_wallet_credits: Decimal | None = None
    delta_credits: Decimal = Decimal(0)
    evidence: dict[str, Any] = field(default_factory=dict)
    manual_reason: str | None = None
    manual_detail: str | None = None

    def fingerprint(self) -> str:
        """逐用户 hash：回滚与复核时用来确认「这条基线就是当时算出来的那条」。"""
        payload = json.dumps(
            {
                'user_id': self.user_id,
                'newapi_user_id': self.newapi_user_id,
                'entitled': str(self.entitled_wallet_credits),
                'consumed': str(self.consumed_wallet_credits),
                'target': str(self.target_wallet_credits),
                'current': str(self.current_wallet_credits),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _to_decimal(value: Any) -> Decimal:
    """把接口回来的数字/字符串转成 Decimal；解析不了就抛，绝不静默当 0。"""
    if value is None:
        raise ValueError('缺少数值')
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f'无法解析为数值: {value!r}') from exc


def build_baseline(
    *,
    user_id: int,
    newapi_user_id: int,
    entitled_credits: Decimal,
    evidence: dict[str, Any],
    consumption: dict[str, Any],
    account: dict[str, Any] | None,
) -> UserBaseline:
    """纯计算：由「应得额度 + 消费汇总 + 当前账户」推出目标基线与增量。

    这一层刻意不碰 IO，方便用真实数据形状做单测。任一步判定不了就写 ``manual_reason``，
    由调用方分流到人工清单。
    """
    baseline = UserBaseline(
        user_id=user_id,
        newapi_user_id=newapi_user_id,
        entitled_wallet_credits=entitled_credits,
        evidence=dict(evidence),
    )

    # 一、消费必须能归因。NewAPI 说不确定，就是不确定——不摊派、不估算。
    if not consumption.get('determinate'):
        baseline.manual_reason = REASON_CONSUMPTION_INDETERMINATE
        baseline.manual_detail = str(
            consumption.get('indeterminate_reason') or '历史请求日志缺少资金池拆分明细，无法判定永久钱包实际消费'
        )
        baseline.evidence['unattributed_credits'] = consumption.get('unattributed_credits')
        baseline.evidence['unattributed_count'] = consumption.get('unattributed_count')
        return baseline

    baseline.consumed_wallet_credits = _to_decimal(consumption.get('wallet_consumed_credits'))
    baseline.evidence['subscription_consumed_credits'] = consumption.get('subscription_consumed_credits')
    baseline.target_wallet_credits = entitled_credits - baseline.consumed_wallet_credits

    # 二、算出负数说明存在超额放行或凭证缺失——正是本次事故的形态。
    #     必须人工判定该不该追、追多少；静默归零会把平台的账错记到用户头上。
    if baseline.target_wallet_credits < 0:
        baseline.manual_reason = REASON_NEGATIVE_TARGET
        baseline.manual_detail = (
            f'目标余额为负（应得 {format_credits(entitled_credits)}，'
            f'已消费 {format_credits(baseline.consumed_wallet_credits)}）：'
            '存在超额放行或凭证缺失，需人工判定，绝不静默归零'
        )
        return baseline

    # 三、拿不到当前余额就算不出增量。
    if account is None:
        baseline.manual_reason = REASON_ACCOUNT_UNREADABLE
        baseline.manual_detail = 'NewAPI 账户读取失败，无法计算增量'
        return baseline

    baseline.current_wallet_credits = _to_decimal((account.get('wallet') or {}).get('remaining_credits'))
    baseline.delta_credits = baseline.target_wallet_credits - baseline.current_wallet_credits
    return baseline


async def _load_entitled_wallet_credits() -> dict[int, tuple[Decimal, dict[str, Any]]]:
    """从不可变商业凭证重建每个用户「应得的永久额度」。"""
    result: dict[int, tuple[Decimal, dict[str, Any]]] = {}
    async with async_db_session() as db:
        # 1) 已支付且未退款的积分包订单：按订单里固化的积分数量，不按支付金额反推。
        #    退款订单在 C2 里会被标成 fulfillment_status='reversed'，这里据此排除。
        rows = await db.execute(
            text("""
                SELECT user_id,
                       COALESCE(SUM((extra_data ->> 'credit_amount')::numeric), 0) AS credits,
                       COUNT(*) AS order_count
                FROM hasn_billing.pay_order
                WHERE status = :paid
                  AND (offering_ref ->> 'kind') = 'credit_pack'
                  AND extra_data ? 'credit_amount'
                  AND COALESCE(fulfillment_status, '') <> 'reversed'
                GROUP BY user_id
            """),
            {'paid': ORDER_PAID},
        )
        for user_id, credits, order_count in rows.all():
            result[int(user_id)] = (Decimal(str(credits or 0)), {'credit_pack_orders': int(order_count)})

        # 2) 合法的 admin 赠送、注册奖励与活动奖励：以成功的履约事件为准，回收事件相抵。
        rows = await db.execute(
            text("""
                SELECT user_id,
                       COALESCE(SUM(CASE WHEN event_type = 'wallet_grant' THEN applied_credits
                                         ELSE -applied_credits END), 0) AS credits,
                       COUNT(*) AS event_count
                FROM hasn_billing.credit_grant_event
                WHERE status = 'succeeded'
                  AND event_type IN ('wallet_grant', 'wallet_revoke')
                  AND applied_credits IS NOT NULL
                  AND (idempotency_key LIKE 'admin:%'
                       OR idempotency_key LIKE 'bonus:%'
                       OR idempotency_key LIKE 'signup:%')
                GROUP BY user_id
            """)
        )
        for user_id, credits, event_count in rows.all():
            base, evidence = result.get(int(user_id), (Decimal(0), {}))
            evidence['grant_events'] = int(event_count)
            result[int(user_id)] = (base + Decimal(str(credits or 0)), evidence)
    return result


async def _load_mappings() -> dict[int, int]:
    async with async_db_session() as db:
        rows = await db.execute(
            text("""
                SELECT huanxing_user_id, newapi_user_id
                FROM hasn_billing.llm_newapi_user_mapping
                WHERE status = 'active' AND newapi_user_id IS NOT NULL
            """)
        )
        return {int(u): int(n) for u, n in rows.all()}


async def _fetch_consumption(newapi_user_id: int) -> dict[str, Any]:
    try:
        return await newapi_credit_client.get_consumption_summary(newapi_user_id)
    except NewApiCreditError as exc:
        log.warning(f'[Rebase] 读取消费汇总失败 newapi_user_id={newapi_user_id}: {exc}')
        # 读不到就是判定不了，走人工清单；不猜。
        return {'determinate': False, 'indeterminate_reason': f'消费汇总读取失败：{exc}'}


async def _fetch_account(newapi_user_id: int) -> dict[str, Any] | None:
    try:
        return await newapi_credit_client.get_credit_account(newapi_user_id)
    except NewApiCreditError as exc:
        log.warning(f'[Rebase] 读取 NewAPI 账户失败 newapi_user_id={newapi_user_id}: {exc}')
        return None


async def compute_baselines(
    *,
    fetch_consumption: Callable[[int], Awaitable[dict[str, Any]]] | None = None,
    fetch_account: Callable[[int], Awaitable[dict[str, Any] | None]] | None = None,
) -> tuple[list[UserBaseline], list[UserBaseline]]:
    """计算全量基线。返回 ``(可自动写入的, 需人工复核的)``。"""
    consumption_fetcher = fetch_consumption or _fetch_consumption
    account_fetcher = fetch_account or _fetch_account

    entitled = await _load_entitled_wallet_credits()
    mappings = await _load_mappings()

    applicable: list[UserBaseline] = []
    manual: list[UserBaseline] = []

    for user_id, newapi_user_id in sorted(mappings.items()):
        credits, evidence = entitled.get(user_id, (Decimal(0), {}))
        consumption = await consumption_fetcher(newapi_user_id)
        account = await account_fetcher(newapi_user_id) if consumption.get('determinate') else None
        baseline = build_baseline(
            user_id=user_id,
            newapi_user_id=newapi_user_id,
            entitled_credits=credits,
            evidence=evidence,
            consumption=consumption,
            account=account,
        )
        (manual if baseline.manual_reason else applicable).append(baseline)

    return applicable, manual


def build_report(
    batch: str,
    applicable: list[UserBaseline],
    manual: list[UserBaseline],
    *,
    applied: bool,
    generated_at: str,
) -> dict[str, Any]:
    """生成可归档的批次报告。

    报告本身就是**可恢复备份**：``current_credits`` 记录了改动前的余额，
    出问题时照同一份报告反向发一批增量即可回到原状。
    """
    return {
        'batch': batch,
        'generated_at': generated_at,
        'applied': applied,
        'applicable_count': len(applicable),
        'manual_review_count': len(manual),
        'applicable': [
            {
                'user_id': b.user_id,
                'newapi_user_id': b.newapi_user_id,
                'entitled_credits': format_credits(b.entitled_wallet_credits),
                'consumed_credits': format_credits(b.consumed_wallet_credits),
                'target_credits': format_credits(b.target_wallet_credits),
                # 改动前余额：回滚就靠它
                'current_credits': format_credits(b.current_wallet_credits or Decimal(0)),
                'delta_credits': format_credits(b.delta_credits),
                'fingerprint': b.fingerprint(),
                'evidence': b.evidence,
            }
            for b in applicable
        ],
        'manual_review': [
            {
                'user_id': b.user_id,
                'newapi_user_id': b.newapi_user_id,
                'reason': b.manual_reason,
                'detail': b.manual_detail,
                'entitled_credits': format_credits(b.entitled_wallet_credits),
                'evidence': b.evidence,
            }
            for b in manual
        ],
    }


async def apply_baselines(batch: str, baselines: list[UserBaseline]) -> dict[str, int]:
    """把基线增量写入 NewAPI。

    走**幂等履约事件**（``admin:REBASE-{batch}-{user}:wallet-grant``），
    不使用任何「设置绝对 quota」的入口。同一批次重跑不会二次调整。
    """
    from backend.app.billing.service.credit_grant_service import credit_grant_service

    summary = {'granted': 0, 'revoked': 0, 'skipped': 0, 'failed': 0}
    for baseline in baselines:
        if baseline.delta_credits == 0:
            summary['skipped'] += 1
            continue
        doc_no = f'REBASE-{batch}-{baseline.user_id}'
        try:
            async with async_db_session.begin() as db:
                if baseline.delta_credits > 0:
                    await credit_grant_service.admin_grant(
                        db,
                        user_id=baseline.user_id,
                        credits=baseline.delta_credits,
                        grant_no=doc_no,
                        reason=f'rebase:{batch}',
                    )
                    summary['granted'] += 1
                else:
                    await credit_grant_service.admin_revoke(
                        db,
                        user_id=baseline.user_id,
                        credits=-baseline.delta_credits,
                        revoke_no=doc_no,
                        reason=f'rebase:{batch}',
                    )
                    summary['revoked'] += 1
        except Exception as exc:
            log.error(f'[Rebase] 写入基线失败 user_id={baseline.user_id}: {exc!r}')
            summary['failed'] += 1
    return summary


async def main_async(batch: str, out_dir: pathlib.Path, apply: bool) -> dict[str, Any]:
    applicable, manual = await compute_baselines()
    report = build_report(
        batch,
        applicable,
        manual,
        applied=apply,
        generated_at=datetime.now(dt_timezone.utc).isoformat(),
    )

    # 先落盘再写入：报告即备份，必须早于任何改动存在于磁盘上。
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f'rebase-{batch}.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')

    if apply:
        report['apply_summary'] = await apply_baselines(batch, applicable)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description='存量余额一次性 rebase（默认只出报告）')
    parser.add_argument('--batch', required=True, help='批次号（幂等键组件，重跑同批次不会二次调整）')
    parser.add_argument('--out', required=True, help='报告输出目录')
    parser.add_argument(
        '--apply',
        action='store_true',
        help='真正写入基线。不加此参数只出报告，不写任何数据。',
    )
    args = parser.parse_args()

    report = asyncio.run(main_async(args.batch, pathlib.Path(args.out), args.apply))
    print(json.dumps({k: v for k, v in report.items() if k != 'applicable'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
