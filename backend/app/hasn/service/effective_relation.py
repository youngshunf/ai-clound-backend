"""有效关系解析（doc08 §4.1.1 的纯判定核心，无 DB / 无副作用）。

事实源：docs/hasn-node设计文档/05-安全与权限/08-关系类型与信任等级完整定义与实现.md §4.1

「要通信，必须先建立联系人关系」。分身↔人、分身↔分身互发消息前，接收方侧必须存在（或可按
主人关系派生出）一条有效关系。本模块只做**纯判定**：给定「直连实体边信任」+「发送方主人边
信任」，产出三分叉决策（送达 / 代发请求并暂存 / 门控）+ 物化边应继承的信任等级。副作用
（物化边、代发好友请求、暂存拦截箱）由调用方 inbound_gatekeeper / message_router 执行。

抽成纯函数是为了可被入站门控（A2A）、A2H 判定、消息工具三处共用，且能脱离 DB 单测三分叉。
"""

from __future__ import annotations

# 决策分叉（与 inbound_gatekeeper 的 ALLOW/SUPPRESS 动作区分，命名独立避免混淆）
DELIVER = 'deliver'  # 直连边≥2 命中，或主人边≥3 派生物化 → 直接送达
REQUEST_AND_SUPPRESS = 'request_and_suppress'  # 主人边=2 普通朋友 → 代发好友请求 + 暂存拦截箱
GATE = 'gate'  # 陌生人 / 无边 / 低档 → 走现状门控（deny→intercept）
BLOCKED = 'blocked'  # 黑名单 → 静默拒（不进箱，不暴露拉黑事实）

# 普通朋友档：矩阵 social[2] send_message=ALLOW，是「关系建立」的起点档
NORMAL_TRUST_LEVEL = 2
# 好友档下限：≥3（好友/密友）才自动物化并直达（D7 继承主人档）
FRIEND_TRUST_FLOOR = 3


def resolve_effective_relation(
    *,
    from_is_agent: bool,
    direct_edge_trust: int | None,
    direct_edge_blocked: bool = False,
    owner_edge_trust: int | None = None,
    owner_edge_blocked: bool = False,
) -> dict[str, object]:
    """有效关系解析（§4.1.1 主人派生的纯判定核）。

    入参：
        from_is_agent       发送方实体是否为分身（否则不做主人派生，无边即陌生人门控）。
        direct_edge_trust   接收方主人对**发送方实体**的直连边信任（None=无直连边）。
        direct_edge_blocked 直连边是否黑名单（trust=0 或 status=blocked）。
        owner_edge_trust    接收方主人对**发送方主人**的 social 边信任（None=无边）。
        owner_edge_blocked  主人边是否黑名单。

    返回 {"decision": DELIVER|REQUEST_AND_SUPPRESS|GATE|BLOCKED, "inherit_trust": int|None}：
        - DELIVER：inherit_trust 非空表示需**物化** `A→s` 边并写该信任（派生≥3 继承主人档，D7）；
          inherit_trust 为空表示直连边已命中、无需物化。
        - REQUEST_AND_SUPPRESS：inherit_trust=2（代发的好友请求档=普通朋友）。
        - GATE / BLOCKED：inherit_trust 恒 None。

    fail-closed：任何「无法判定」一律落到 GATE（按陌生人处置），绝不放宽。
    """
    # ── 1. 直连实体边优先（现状唯一路径，§4.1.1 step 1）──
    if direct_edge_blocked:
        return {'decision': BLOCKED, 'inherit_trust': None}
    if direct_edge_trust is not None:
        # 有直连边就用它，不再回退主人派生（尊重主人对该实体的显式设档）
        if direct_edge_trust >= NORMAL_TRUST_LEVEL:
            return {'decision': DELIVER, 'inherit_trust': None}
        # 直连边=1（陌生人档）→ 门控
        return {'decision': GATE, 'inherit_trust': None}

    # ── 2. 无直连边：仅分身发送方做主人派生（§4.1.1 step 2）──
    if not from_is_agent:
        # 人发的无边消息 = 纯陌生人，走门控（不派生）
        return {'decision': GATE, 'inherit_trust': None}
    if owner_edge_blocked:
        return {'decision': BLOCKED, 'inherit_trust': None}
    if owner_edge_trust is None:
        return {'decision': GATE, 'inherit_trust': None}
    if owner_edge_trust >= FRIEND_TRUST_FLOOR:
        # ≥3 好友/密友 → 自动物化，trust 继承主人档（D7）
        return {'decision': DELIVER, 'inherit_trust': owner_edge_trust}
    if owner_edge_trust == NORMAL_TRUST_LEVEL:
        # =2 普通朋友 → 代发好友请求 + 暂存拦截箱
        return {'decision': REQUEST_AND_SUPPRESS, 'inherit_trust': NORMAL_TRUST_LEVEL}
    # owner_edge_trust <= 1（陌生人/无档）→ 门控
    return {'decision': GATE, 'inherit_trust': None}
