"""版本闸纯函数测试（R2-10·§8.3-2 掉队客户端闸）。

覆盖 doc92 R2-10 验收「低版本模拟握手被拒且错误码可被 D3 消费」的判定内核：
- `parse_version`：点分版本 → 可比较元组，宽松容忍 v 前缀 / 预发布后缀 / 空段；
- `is_below_minimum`：闸关（阈值空/无效）放行一切；阈值有效时低版本 True、等/高版本 False；
  **fail-closed**——阈值非空但客户端版本缺失/不可解析一律判为过低（True）；
- 拒连错误码契约：`UPGRADE_REQUIRED_CLOSE_CODE=4003` + reason 前缀 `UPGRADE_REQUIRED`（D3 据此识别）。

纯函数，无 DB / 无 WebSocket——直接单测（无需真 PG）。
"""

from __future__ import annotations

import pytest

from backend.app.hasn_im.protocol.version_gate import (
    UPGRADE_REQUIRED_CLOSE_CODE,
    UPGRADE_REQUIRED_REASON_PREFIX,
    build_upgrade_required_reason,
    is_below_minimum,
    parse_version,
)


@pytest.mark.parametrize(
    'raw, expected',
    [
        ('1.4.0', (1, 4, 0)),
        ('1.4', (1, 4)),
        ('v2.0.1', (2, 0, 1)),
        ('V2', (2,)),
        ('1.4.0-rc1', (1, 4, 0)),  # 预发布后缀被丢弃
        ('1.4.0+build.7', (1, 4, 0)),  # 构建元数据被丢弃
        ('  1.4.0  ', (1, 4, 0)),  # 去空白
        ('3rc', (3,)),  # 段内前导数字
        ('10.20.30', (10, 20, 30)),  # 多位数按整数比较（非字典序）
    ],
)
def test_parse_version_ok(raw, expected):
    assert parse_version(raw) == expected


@pytest.mark.parametrize('raw', [None, '', '   ', 'v', 'abc', 'rc1', '.'])
def test_parse_version_unparseable(raw):
    assert parse_version(raw) is None


def test_parse_version_numeric_not_lexical():
    # 多位数按整数比较：10 > 9（字典序会误判 '10' < '9'）。
    assert parse_version('1.10.0') > parse_version('1.9.0')


# ── is_below_minimum ──


@pytest.mark.parametrize('minimum', ['', None, '   ', 'not-a-version'])
def test_gate_disabled_passes_everything(minimum):
    # 阈值空/无效 = 闸关，放行一切（含无版本头的客户端）——本地测试阶段不闸任何版本。
    assert is_below_minimum('1.0.0', minimum) is False
    assert is_below_minimum(None, minimum) is False
    assert is_below_minimum('', minimum) is False


def test_below_minimum_rejected():
    assert is_below_minimum('1.3.9', '1.4.0') is True
    assert is_below_minimum('1.3', '1.4.0') is True
    assert is_below_minimum('0.9.9', '1.0.0') is True


def test_equal_or_above_passes():
    assert is_below_minimum('1.4.0', '1.4.0') is False  # 相等放行
    assert is_below_minimum('1.4.1', '1.4.0') is False  # 更高放行
    assert is_below_minimum('2.0.0', '1.4.0') is False


def test_equal_treats_short_as_zero_padded():
    # '1.4' 视同 '1.4.0'，不低于 '1.4.0'（元组字典序天然处理：(1,4) < (1,4,0) 为 False）。
    assert is_below_minimum('1.4', '1.4.0') is False


def test_fail_closed_missing_client_version_when_threshold_set():
    # 阈值已设但客户端无可解析版本 → fail-closed 拒连（掉队/伪装旧节点必须被闸住，§8.3-2）。
    assert is_below_minimum(None, '1.4.0') is True
    assert is_below_minimum('', '1.4.0') is True
    assert is_below_minimum('garbage', '1.4.0') is True


# ── 拒连错误码契约（D3 消费）──


def test_upgrade_required_close_code_contract():
    # D3 客户端侧据此识别「需要升级」拒连；4001 认证/4002 登出/4003 版本过低互不重叠。
    assert UPGRADE_REQUIRED_CLOSE_CODE == 4003
    assert UPGRADE_REQUIRED_REASON_PREFIX == 'UPGRADE_REQUIRED'


def test_build_reason_carries_prefix_and_diagnostics():
    reason = build_upgrade_required_reason('1.4.0', '1.3.0')
    assert reason.startswith(f'{UPGRADE_REQUIRED_REASON_PREFIX}:')
    assert '1.4.0' in reason  # 需要的最低版本
    assert '1.3.0' in reason  # 当前客户端版本


def test_build_reason_handles_missing_client_version():
    reason = build_upgrade_required_reason('1.4.0', None)
    assert reason.startswith(f'{UPGRADE_REQUIRED_REASON_PREFIX}:')
    assert '未知' in reason
