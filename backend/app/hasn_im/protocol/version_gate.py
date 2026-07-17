"""hasn_im.protocol.version_gate · WS 握手最低客户端版本闸（R2-10·§8.3-2）

旧事件切换后，掉队 daemon 必须被闸住、不能继续收发旧事件（§8.3-2「掉队客户端闸」）。本模块把
「最低版本判定」提为**无 DB 纯函数**：给定客户端上报版本（`X-App-Version` 头）与已拍板阈值
（`settings.HASN_WS_MIN_CLIENT_VERSION`，最低版本 = R3 配套 daemon 版本），返回是否放行；
ws_node 握手处消费判定，低于阈值即以 `UPGRADE_REQUIRED_CLOSE_CODE` 拒连，错误码/reason 供
D3（客户端侧版本闸）识别并出「需要升级」引导 UX、停止重连风暴。

设计约束：
- **阈值空 = 闸关**（默认 `''`）：本地重构/测试阶段不闸任何版本（对齐福仔「本地测试通过最后才生产
  部署」——闸只在 R3 切换即生效）；生产在 R3 窗口把阈值设为配套 daemon 版本。
- **fail-closed**：阈值一旦非空，客户端版本缺失 / 不可解析一律判为「低于阈值」拒连——「掉队节点必须
  被闸住，而不是继续收发旧事件」（§8.3-2）。宽松放行会让无版本头的旧 daemon 蒙混过关。
- 纯 str ↔ bool，**不 import** WebSocket / Session / ORM / settings（阈值由调用方注入）。
"""

from __future__ import annotations

# ── 拒连错误码契约（D3 客户端侧据此识别「需要升级」并停重连风暴、出升级引导）──
# WS 应用级关闭码（4000-4999 保留给应用）：4001=认证失败、4002=登出顶替、4003=版本过低需升级。
UPGRADE_REQUIRED_CLOSE_CODE = 4003
# reason 前缀（机器可判）：D3 见此前缀即走升级引导分支，而非普通断线重连。
UPGRADE_REQUIRED_REASON_PREFIX = 'UPGRADE_REQUIRED'


def parse_version(raw: str | None) -> tuple[int, ...] | None:
    """把点分版本串解析为可比较的整数元组；不可解析返回 None。

    宽松容忍：去首尾空白、去可选 `v` 前缀，先在**整串**截断预发布/构建元数据（首个 `-` 或 `+`
    之后全丢，故 `1.4.0-rc1` / `1.4.0+build.7` 均 → `(1,4,0)`），再按 `.` 切段、每段取前导数字。
    任一有效数字段都取不到（空串 / 纯非数字）→ None（交由 fail-closed 判定）。
    """
    if not raw:
        return None
    text = raw.strip()
    if text[:1] in ('v', 'V'):
        text = text[1:]
    # 截断预发布（`-`）/ 构建元数据（`+`）：其内含的 `.` 不得漏进核心版本段比较。
    for sep in ('-', '+'):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
    text = text.strip()
    if not text:
        return None
    parts: list[int] = []
    for seg in text.split('.'):
        seg = seg.strip()
        # 取该段的前导连续数字（容忍 `3rc` 这类无分隔符的段内后缀）。
        digits = ''
        for ch in seg:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits == '':
            # 该段无前导数字：到此为止（后续段不参与比较）。
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def is_below_minimum(client_version: str | None, minimum: str | None) -> bool:
    """判定客户端版本是否**低于**最低阈值（低于 = 应被版本闸拒连）。

    - 阈值空 / 不可解析（闸关或配置无效）→ 一律放行（`False`），不闸任何客户端；
    - 阈值有效但客户端版本缺失 / 不可解析 → **fail-closed 判为低于阈值**（`True`，拒连）；
    - 两者均可解析 → 元组字典序比较，`client < minimum` 即低于阈值。

    元组长度不齐由字典序天然处理（`(1,2) < (1,2,0)` 为 False——`1.2` 视同 `1.2.0` 不低于）。
    """
    min_tuple = parse_version(minimum)
    if min_tuple is None:
        # 阈值空或无效 = 闸关，放行一切（本地测试/未配置阶段）。
        return False
    client_tuple = parse_version(client_version)
    if client_tuple is None:
        # 阈值已设但客户端无可解析版本——掉队/伪装的旧节点，fail-closed 拒连。
        return True
    # 零填充补齐到等长再比较：`1.4` 视同 `1.4.0`，不因段数不齐被误判为低于
    # （元组直接比较会把较短前缀判为更小，(1,4) < (1,4,0)——须避免）。
    length = max(len(client_tuple), len(min_tuple))
    client_padded = client_tuple + (0,) * (length - len(client_tuple))
    min_padded = min_tuple + (0,) * (length - len(min_tuple))
    return client_padded < min_padded


def build_upgrade_required_reason(minimum: str | None, client_version: str | None) -> str:
    """构造拒连 reason（D3 可判前缀 + 人读诊断）：`UPGRADE_REQUIRED: 需 >=X，当前 Y`。"""
    shown = (client_version or '').strip() or '未知'
    return f'{UPGRADE_REQUIRED_REASON_PREFIX}: 客户端版本过低，需 >= {minimum}，当前 {shown}，请升级后重连'
