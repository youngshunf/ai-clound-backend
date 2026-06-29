"""Owner 记忆合并身份保全纯函数单测（修复「合并抹掉主人昵称/HASN_ID」数据丢失 bug）。

`_ensure_identity_lines` 是合并下发前的身份兜底：LLM 合并可能漏掉建档身份事实（历史上甚至整段
抹掉），这里以权威来源（HasnHumans.nickname + owner_id）把缺失的 称呼/Owner HASN ID 补回，
确保主人昵称/HASN_ID 永不因合并丢失。纯函数（不碰 DB），故为普通同步单测、无需 PG。
"""

from __future__ import annotations

from backend.app.hasn_memory.service.owner_memory_service import _ensure_identity_lines


def test_appends_both_identity_lines_when_missing() -> None:
    """合并结果完全不含身份 → 称呼 + Owner HASN ID 都补到最前（§ 分隔，原内容保留在后）。"""
    merged = '健康: 主人注重抗衰老\n§\n居住: 主人常驻昆明'
    out = _ensure_identity_lines(merged, nickname='福仔', owner_id='h_abc123')

    assert out.startswith('称呼: 福仔\n§\nOwner HASN ID: h_abc123\n§\n')
    assert '健康: 主人注重抗衰老' in out  # 原有事实不丢
    assert '居住: 主人常驻昆明' in out


def test_noop_when_identity_already_present() -> None:
    """合并结果已含称呼与 HASN ID → 原样返回，不重复追加身份行。"""
    merged = '称呼: 福仔\n§\nOwner HASN ID: h_abc123\n§\n健康: 注重抗衰老'
    out = _ensure_identity_lines(merged, nickname='福仔', owner_id='h_abc123')

    assert out == merged
    assert out.count('称呼') == 1
    assert out.count('Owner HASN ID') == 1


def test_matches_nickname_label_variants_and_fullwidth_colon() -> None:
    """「昵称」别名 + 全角冒号也算已含称呼，只补缺失的 HASN ID（不重复称呼）。"""
    merged = '昵称：福仔\n§\n健康: 注重抗衰老'
    out = _ensure_identity_lines(merged, nickname='福仔', owner_id='h_abc123')

    assert 'Owner HASN ID: h_abc123' in out
    # 已有「昵称：」视为含称呼，不再追加「称呼:」行
    assert '称呼:' not in out


def test_adds_only_hasn_id_when_nickname_blank() -> None:
    """新主人尚无昵称（nickname 空）→ 不造假称呼，只兜底 Owner HASN ID。"""
    merged = '健康: 注重抗衰老'
    out = _ensure_identity_lines(merged, nickname='', owner_id='h_abc123')

    assert out.startswith('Owner HASN ID: h_abc123\n§\n')
    assert '称呼' not in out


def test_skips_hasn_id_when_already_in_content() -> None:
    """owner_id 已作为子串出现在内容里（HASN ID 已在）→ 不重复补 Owner HASN ID 行。"""
    merged = 'Owner HASN ID: h_abc123\n§\n健康: 注重抗衰老'
    out = _ensure_identity_lines(merged, nickname='', owner_id='h_abc123')

    assert out == merged
    assert out.count('h_abc123') == 1


def test_returns_pure_identity_when_content_empty() -> None:
    """合并结果为空 → 仅返回身份行（不前置多余的 § 分隔到空内容上）。"""
    out = _ensure_identity_lines('', nickname='福仔', owner_id='h_abc123')

    assert out == '称呼: 福仔\n§\nOwner HASN ID: h_abc123'
    assert not out.endswith('§')
