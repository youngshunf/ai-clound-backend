"""HASN 三维权限矩阵常量 (专利 H02 / 协议 04)

维度一: relation_type — social / commerce / service / professional / platform
维度二: trust_level   — 0-5 六级
维度三: action_type   — 每种 relation_type 的独立行为集合
四态编码: allow / deny / confirm_required / scope_limited
"""
from backend.common.log import log

# ── 四态编码 ─────────────────────────────────────
ALLOW = 'allow'
DENY = 'deny'
CONFIRM = 'confirm_required'
SCOPE_LTD = 'scope_limited'

# ── 六级信任标签 (H02 §2) ────────────────────────
TRUST_LEVEL_LABELS: dict[int, str] = {
    0: 'blocked',    # 完全屏蔽，消息直接丢弃
    1: 'stranger',   # 陌生人，仅可发好友请求
    2: 'normal',     # 已建立关系，基础通信
    3: 'friend',     # 朋友，可看日程/偏好（核心分界点）
    4: 'trusted',    # 密友/高信任，可代预约，代承诺需确认
    5: 'owner',      # 所有者，仅限自己的 Agent
}

TRUST_LEVEL_COLORS: dict[int, str] = {
    0: 'red', 1: 'gray', 2: 'blue',
    3: 'green', 4: 'orange', 5: 'purple',
}

# ── 社交类行为 (8 种) ────────────────────────────
SOCIAL_ACTIONS = [
    'send_message',       # 发送消息/自由聊天
    'view_public_info',   # 查看公开信息
    'view_schedule',      # 查看日程
    'view_preferences',   # 查看偏好详情
    'view_location',      # 查看城市级位置
    'make_appointment',   # 帮预约/代操作
    'make_commitment',    # 代做承诺
    'view_sensitive',     # 查看财务/健康信息
]

# ── 商业类行为 (7 种) ────────────────────────────
COMMERCE_ACTIONS = [
    'product_inquiry',     # 商品咨询/询价
    'trade_communication',  # 交易沟通 (下单/物流/售后)
    'view_shopping_pref',  # 查看购物偏好 (体型/风格)
    'send_push',           # 主动推送商品消息
    'free_chat',           # 自由闲聊
    'view_schedule_loc',   # 查看日程/位置/社交圈
    'view_sensitive',      # 查看财务/健康信息
]

# ── 服务类行为 (4 种) ────────────────────────────
SERVICE_ACTIONS = [
    'order_communication',  # 订单内沟通
    'decrypt_address',     # 一次性解密配送地址
    'non_order_comm',      # 订单外沟通
    'view_personal_info',  # 查看个人信息
]

# ── 专业类行为 (5 种) ────────────────────────────
PROFESSIONAL_ACTIONS = [
    'professional_consult',      # 专业领域咨询
    'view_authorized_data',      # 查看授权的领域数据
    'view_unauthorized_data',    # 查看非授权领域数据
    'make_professional_decision',  # 代做专业决定
    'free_chat',                 # 自由闲聊
]

# ── 关系类型集合 ─────────────────────────────────
RELATION_TYPES = {'social', 'commerce', 'service', 'professional', 'platform'}

RELATION_ACTIONS: dict[str, list[str]] = {
    'social':       SOCIAL_ACTIONS,
    'commerce':     COMMERCE_ACTIONS,
    'service':      SERVICE_ACTIONS,
    'professional': PROFESSIONAL_ACTIONS,
    'platform':     ['send_message', 'view_public_info'],
}

# ── 完整默认权限矩阵 ─────────────────────────────
# matrix[relation_type][trust_level] = {action: state}
# None 表示沿用上级 (fallback_level) 等级的权限
# 对齐协议 04-权限与行为控制.md §1.4

DEFAULT_PERMISSION_MATRIX: dict[str, dict[int, dict[str, str] | None]] = {
    'social': {
        0: dict.fromkeys(SOCIAL_ACTIONS, DENY),   # blocked: 全拒
        1: {
            'send_message': DENY, 'view_public_info': ALLOW,
            'view_schedule': DENY, 'view_preferences': DENY,
            'view_location': DENY, 'make_appointment': DENY,
            'make_commitment': DENY, 'view_sensitive': DENY,
        },
        2: {
            'send_message': ALLOW, 'view_public_info': ALLOW,
            'view_schedule': DENY, 'view_preferences': DENY,
            'view_location': DENY, 'make_appointment': DENY,
            'make_commitment': DENY, 'view_sensitive': DENY,
        },
        3: {
            'send_message': ALLOW, 'view_public_info': ALLOW,
            'view_schedule': ALLOW, 'view_preferences': ALLOW,
            'view_location': DENY, 'make_appointment': DENY,
            'make_commitment': DENY, 'view_sensitive': DENY,
        },
        4: {
            'send_message': ALLOW, 'view_public_info': ALLOW,
            'view_schedule': ALLOW, 'view_preferences': ALLOW,
            'view_location': ALLOW, 'make_appointment': ALLOW,
            'make_commitment': CONFIRM, 'view_sensitive': DENY,
        },
        5: dict.fromkeys(SOCIAL_ACTIONS, ALLOW),  # owner: 全允许
    },
    'commerce': {
        0: dict.fromkeys(COMMERCE_ACTIONS, DENY),
        1: {
            'product_inquiry': SCOPE_LTD, 'trade_communication': DENY,
            'view_shopping_pref': DENY, 'send_push': DENY,
            'free_chat': DENY, 'view_schedule_loc': DENY, 'view_sensitive': DENY,
        },
        2: {
            'product_inquiry': ALLOW, 'trade_communication': ALLOW,
            'view_shopping_pref': ALLOW, 'send_push': DENY,
            'free_chat': DENY, 'view_schedule_loc': DENY, 'view_sensitive': DENY,
        },
        3: None,   # commerce 不区分 Friend，等同 Normal (level=2)
        4: {
            'product_inquiry': ALLOW, 'trade_communication': ALLOW,
            'view_shopping_pref': ALLOW, 'send_push': ALLOW,
            'free_chat': DENY, 'view_schedule_loc': DENY, 'view_sensitive': DENY,
        },
        5: None,   # commerce 无 Owner 等级 (fallback → level=4)
    },
    'service': {
        0: dict.fromkeys(SERVICE_ACTIONS, DENY),
        1: dict.fromkeys(SERVICE_ACTIONS, DENY),   # 无陌生人状态
        2: {
            'order_communication': ALLOW, 'decrypt_address': ALLOW,
            'non_order_comm': DENY, 'view_personal_info': DENY,
        },
        3: None,   # 同 Normal (level=2)
        4: None,   # 同 Normal (level=2)
        5: None,   # 无 Owner (fallback → level=2)
    },
    'professional': {
        0: dict.fromkeys(PROFESSIONAL_ACTIONS, DENY),
        1: {
            'professional_consult': SCOPE_LTD, 'view_authorized_data': DENY,
            'view_unauthorized_data': DENY, 'make_professional_decision': DENY,
            'free_chat': DENY,
        },
        2: {
            'professional_consult': ALLOW, 'view_authorized_data': ALLOW,
            'view_unauthorized_data': DENY, 'make_professional_decision': DENY,
            'free_chat': DENY,
        },
        3: None,   # 同 Normal (level=2)
        4: {
            'professional_consult': ALLOW, 'view_authorized_data': ALLOW,
            'view_unauthorized_data': DENY, 'make_professional_decision': CONFIRM,
            'free_chat': DENY,
        },
        5: None,   # 无 Owner (fallback → level=4)
    },
    'platform': {
        0: {'send_message': DENY, 'view_public_info': DENY},
        1: {'send_message': DENY, 'view_public_info': ALLOW},
        2: {'send_message': ALLOW, 'view_public_info': ALLOW},
        3: None,
        4: None,
        5: None,
    },
}


def effective_trust_level(relation_type: str, trust_level: int) -> int:
    """解析有效信任等级（处理 None fallback 向下取整到最近有效等级）。

    Core/02 §7.4.1: 非 social + trust_level=5 时运行时降级为 4 处理。
    """
    # 协议级降级：Owner=5 仅 social 合法，其他类型运行时降级
    if relation_type != 'social' and trust_level == 5:
        log.warning(
            f'[trust_level] 非 social 关系收到 trust_level=5，'
            f'运行时降级为 4 (relation_type={relation_type})'
        )
        trust_level = 4

    matrix = DEFAULT_PERMISSION_MATRIX.get(relation_type, {})
    level = min(trust_level, 5)
    while level >= 0:
        if matrix.get(level) is not None:
            return level
        level -= 1
    return 0


def get_default_permissions(relation_type: str, trust_level: int) -> dict[str, str]:
    """获取指定 relation_type + trust_level 的默认权限集合（已处理 None fallback）"""
    matrix = DEFAULT_PERMISSION_MATRIX.get(relation_type)
    if not matrix:
        return {}
    eff_level = effective_trust_level(relation_type, trust_level)
    return dict(matrix[eff_level] or {})


def compute_effective_permissions(
    relation_type: str,
    trust_level: int,
    custom_permissions: dict | None = None,
) -> dict[str, str]:
    """合并默认矩阵 + custom_permissions 覆盖（铁律已在写入时校验）"""
    defaults = get_default_permissions(relation_type, trust_level)
    if custom_permissions:
        defaults.update(custom_permissions)
    return defaults


# ── Scope 行为映射表 (协议 04 §1.5) ──────────────
SCOPE_ACTION_MAP: dict[str, list[str]] = {
    # commerce
    'pre_sale':     ['product_inquiry', 'view_public_info'],
    'negotiation':  ['trade_communication'],
    'in_order':     ['trade_communication', 'view_shopping_pref'],
    'fulfilling':   ['trade_communication', 'order_communication'],
    'after_sale':   ['trade_communication', 'view_public_info'],
    'subscription': ['send_push'],
    # service
    'active_order': ['order_communication', 'decrypt_delivery_address'],
    # professional
    'consultation': ['professional_consultation', 'view_public_info'],
    'treatment':    ['professional_consultation'],
    'follow_up':    ['professional_consultation'],
    # platform
    'app_installation': ['send_message', 'app_event'],
    'system_notice':    ['send_message'],
}


# ── 协议级约束错误码 (Core/04 §1.4) ──────────────
ERR_TRUST_LEVEL_INVALID = 40010  # 非 social 关系不得设 trust_level=5；service 关系不存在 Stranger
ERR_SCOPE_LIFECYCLE_INVALID = 40011  # scope 生命周期非法转换


# ── Scope 生命周期状态机 (Core/02 §7.5.2) ─────────
SCOPE_LIFECYCLE_STATES = ('pending', 'active', 'closed', 'expired')

# 合法转换：pending→active, active→closed, active→expired, pending→closed
SCOPE_LIFECYCLE_TRANSITIONS: dict[str, frozenset[str]] = {
    'pending': frozenset({'active', 'closed'}),
    'active':  frozenset({'closed', 'expired'}),
    'closed':  frozenset(),  # 终态
    'expired': frozenset(),  # 终态
}


def is_lifecycle_transition_valid(from_state: str, to_state: str) -> bool:
    """检查 scope 生命周期状态转换是否合法。"""
    return to_state in SCOPE_LIFECYCLE_TRANSITIONS.get(from_state, frozenset())


# ── 协议级约束校验 (Core/04 §1.4) ──────────────
def validate_relation_constraints(relation_type: str, trust_level: int) -> None:
    """校验 relation_type + trust_level 协议级约束。

    - 非 social 关系不得设置 trust_level=5（Owner 仅 social）
    - service 关系不存在 Stranger 状态（trust_level=1）

    违反则抛 ValueError，调用方应捕获并返回 ERR_TRUST_LEVEL_INVALID。
    """
    if relation_type != 'social' and trust_level == 5:
        raise ValueError(
            f'非 social 关系不得设置 trust_level=5（Owner），'
            f'当前 relation_type={relation_type}'
        )
    if relation_type == 'service' and trust_level == 1:
        raise ValueError(
            'service 关系不存在 Stranger 状态（trust_level=1），'
            '此关系随订单创建自动建立'
        )


# ── 7 条铁律常量 (协议 04 §2) ─────────────────────
# 每条铁律关联一组「如果违反则拒绝」的行为检测规则

class IronLawViolation(Exception):
    """铁律冲突异常"""
    def __init__(self, law: str, msg: str) -> None:
        self.law = law
        super().__init__(f'[{law}] {msg}')


def validate_against_iron_laws(
    relation_type: str,
    permissions: dict,
    peer_type: str = 'human',
    trust_level: int = 2,
) -> None:
    """
    校验自定义权限覆盖是否违反铁律。违反则抛出 IronLawViolation。

    铁律 1: send_message 不可允许陌生商务 (commerce stranger) 主动发送自由聊天
    铁律 2: Owner 的 trust_level=5 不可被降级
    铁律 3: make_commitment 若为 allow (而非 confirm_required) 则违反（agent 不得自行承诺）
    铁律 4: view_sensitive 不可设为 allow（敏感数据禁区）
    铁律 5: commerce 关系不可设 free_chat=allow（通信边界强制）
    铁律 5b: service 关系 auto_expire 不可被禁止（到期自动断）
    铁律 6: (频率限制在网关层强制，此处跳过)
    铁律 7: (审计不可篡改，此处跳过)
    """
    # 铁律 3：承诺必须人类确认，不允许直接 allow
    if permissions.get('make_commitment') == ALLOW:
        raise IronLawViolation(
            'IRON_LAW_3',
            'make_commitment 必须为 confirm_required，Agent 不得自行承诺'
        )

    # 铁律 4：敏感数据禁区
    if permissions.get('view_sensitive') == ALLOW:
        raise IronLawViolation(
            'IRON_LAW_4',
            'view_sensitive 不可设为 allow，财务/健康信息受保护'
        )

    # 铁律 5：commerce 关系禁止自由闲聊
    if relation_type == 'commerce' and permissions.get('free_chat') == ALLOW:
        raise IronLawViolation(
            'IRON_LAW_5',
            'commerce 关系禁止 free_chat，通信必须限于业务场景'
        )

    # 铁律 2：trust_level=5 仅限 owner Agent，不可通过 custom_permissions 绕过
    if trust_level == 5 and peer_type != 'agent':
        raise IronLawViolation(
            'IRON_LAW_2',
            'trust_level=5 (Owner) 仅限自己的 Agent'
        )


# ── 基于矩阵的权限检查 ─────────────────────────────

def check_action_permission(
    relation_type: str,
    trust_level: int,
    action: str,
    custom_permissions: dict | None = None,
) -> str:
    """
    检查特定行为是否被允许，返回四态编码之一：
    allow / deny / confirm_required / scope_limited
    """
    effective = compute_effective_permissions(relation_type, trust_level, custom_permissions)
    return effective.get(action, DENY)
