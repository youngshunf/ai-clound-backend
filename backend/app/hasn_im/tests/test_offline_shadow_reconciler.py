"""方案 B 离线 Redis 影子与 PostgreSQL sync 对账测试。"""

from __future__ import annotations

from backend.app.hasn_im.adapters.routing.offline_frame_policy import (
    OfflineFrameCategory,
    OfflineFrameDecision,
    OfflineFramePolicy,
)
from backend.app.hasn_im.observability.offline_shadow_reconciler import (
    ShadowIdentity,
    compare_shadow_identities,
    merge_recent_and_historical_matches,
    shadow_event_type,
)


def test_shadow_identity_comparison_distinguishes_three_sets() -> None:
    """对账必须分别给出 Redis 独有、sync 独有与两边都有。"""
    shared = ShadowIdentity(
        owner_id='h_owner',
        event_type='message.new',
        identity='msg-shared',
    )
    redis_only = ShadowIdentity(
        owner_id='h_owner',
        event_type='task.exec',
        identity='task:run:1:exec',
    )
    sync_only = ShadowIdentity(
        owner_id='h_owner',
        event_type='conversation.updated',
        identity='evt-sync-only',
    )

    report = compare_shadow_identities(
        redis_identities={shared, redis_only},
        sync_identities={shared, sync_only},
        snapshot_backed=2,
        malformed=1,
    )

    assert report.redis_candidates == 2
    assert report.sync_candidates == 2
    assert report.both == 1
    assert report.redis_only == 1
    assert report.sync_only == 1
    assert report.snapshot_backed == 2
    assert report.malformed == 1
    assert report.redis_only_unrecoverable == 1


def test_historical_sync_only_repairs_current_redis_candidate() -> None:
    """历史 sync 只补当前 Redis 候选，不扩大七天 sync-only 集合。"""
    current = ShadowIdentity(
        owner_id='h_owner',
        event_type='message.new',
        identity='msg-current',
    )
    recent_only = ShadowIdentity(
        owner_id='h_owner',
        event_type='conversation.updated',
        identity='evt-recent',
    )
    historical_unrelated = ShadowIdentity(
        owner_id='h_owner',
        event_type='task.exec',
        identity='task:run:old:exec',
    )

    merged = merge_recent_and_historical_matches(
        redis_identities={current},
        recent_sync_identities={recent_only},
        historical_sync_identities={current, historical_unrelated},
    )

    assert merged == {current, recent_only}


def test_future_gap_is_never_misclassified_as_snapshot_backed() -> None:
    """未来新增 gap 帧必须落入不可恢复对账，不得伪装成权威快照覆盖。"""
    policy = OfflineFramePolicy(
        method='hasn.future.command',
        category=OfflineFrameCategory.GAP,
        identity_fields=('command_id',),
        durable_source='尚无',
        recovery_path='尚无',
    )
    decision = OfflineFrameDecision(
        method=policy.method,
        category=policy.category,
        identity='cmd-1',
        policy=policy,
    )

    assert shadow_event_type(decision) == 'gap:hasn.future.command'
