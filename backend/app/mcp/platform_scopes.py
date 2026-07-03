"""Platform 域 scope 展示元数据（catalog 中文化 / 分组 / 风险展示）。

设计事实源：14-平台工具集设计 §3；16-工具授权统一 D-v3-3（scopes.py 退役为权威声明源，
平台域元数据下沉本文件，app 域元数据下沉各应用目录的 `scopes.py`）。

判定真相仍是各 `BaseTool.required_scopes` + 三态 mode；本表只负责**展示元数据**
（中文 label / domain / risk / 描述），通过 scope key 关联。risk 仅 UI 提示（不强制确认，D4）。
"""

from __future__ import annotations

# 仅 platform 工具域（user/contact/message/marketplace + 历史默认词表）。
# app 工具（community/knowledge/deck/task/workflow/publish）的 scope 元数据在各应用目录的 scopes.py。
PLATFORM_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    # —— platform（14-doc §3 权威）——
    'user:search': {'label_zh': '搜索用户', 'domain': 'user', 'risk': 'low', 'description': '按唤星号/昵称搜索 HASN 用户（人或 Agent）'},
    'user:read': {'label_zh': '查看用户资料', 'domain': 'user', 'risk': 'low', 'description': '查看用户/Agent 主页详情'},
    'contact:read': {'label_zh': '查看联系人', 'domain': 'contact', 'risk': 'low', 'description': '列出主人语境下的联系人与关系状态'},
    'contact:request': {'label_zh': '发送联系请求', 'domain': 'contact', 'risk': 'low', 'description': '向某用户发起加联系/好友请求'},
    'message:read': {'label_zh': '读取/搜索聊天记录', 'domain': 'message', 'risk': 'low', 'description': '读取会话历史、跨会话搜索聊天记录'},
    'message:send': {'label_zh': '发送消息', 'domain': 'message', 'risk': 'low', 'description': '给用户/Agent/会话发消息（走真实路由与关系门控）'},
    # —— platform · marketplace（15-技能市场/11-doc 权威源）——
    'marketplace:read': {'label_zh': '浏览能力市场', 'domain': 'marketplace', 'risk': 'low', 'description': '搜索/查看技能与模板、列出当前 Agent 已安装技能'},
    'marketplace:install': {'label_zh': '安装/卸载技能', 'domain': 'marketplace', 'risk': 'medium', 'description': '把市场技能装到当前 Agent 或从中卸载（云端权威 + 重物化）'},
    'marketplace:publish': {'label_zh': '打包与发布资源', 'domain': 'marketplace', 'risk': 'high', 'description': '打包本地技能/模板并发布为当前用户资源（默认草稿；公开/送审过主人确认）'},
    # —— platform · 媒体（hasn-mcp 本地媒体工具，直连 new-api）——
    # 键统一（实施102 S2）：旧 image:generate 死键已删，图片与语音生成统一到 media:generate；
    # 视频单价远高、独立授权档 video:generate 保留。二者均本地工具（source=Local），出厂 Ask（花 owner 配额）。
    'media:generate': {'label_zh': '图片与语音生成', 'domain': 'media', 'risk': 'medium', 'default_mode': 'ask', 'description': '直连唤星 new-api 生成图片（image）与语音（TTS），消耗 owner 配额，故默认每次询问'},
    'video:generate': {'label_zh': '生成视频', 'domain': 'video', 'risk': 'high', 'default_mode': 'ask', 'description': '直连唤星 new-api 视频 API（task 式异步：提交→轮询）生成视频，单价远高于图片、消耗 owner 配额，故独立授权档'},
    # —— platform · 资产（hasn.asset.create：分身把自己的内容上传成 hasn://asset）——
    'asset:create': {'label_zh': '上传媒体资产', 'domain': 'asset', 'risk': 'medium', 'description': '把分身的内容（SVG/图片/文件等）上传到私有桶并注册资产，供消息附件引用'},
    # —— platform · 工作会话（hasn.session.ask：分身在工作会话里主动向主人提问、挂起会话等答复）——
    'session:ask': {'label_zh': '向主人提问', 'domain': 'session', 'risk': 'low', 'description': '工作会话中需主人决策/补关键信息时挂起会话、投提问卡到主会话等主人答复（绝不替主人臆测）'},
    # —— platform · 规划（hasn.plan.* 云端 CRUD：分身代主人管理目标/计划/待办/日程/习惯，模块 19）——
    # 注：plan:schedule / plan:delegate 仍属本地 hasn-mcp 工具（schedule/reschedule/delegate 保留本地），
    # 其展示元数据在本地 scope 词表，不在此平台目录。
    'plan:write': {'label_zh': '编辑规划', 'domain': 'plan', 'risk': 'low', 'description': '代主人建/改/删目标、计划、待办、日程、习惯（读类无需授权）'},
    'plan:read': {'label_zh': '读团队忙闲', 'domain': 'plan', 'risk': 'low', 'description': '读企业成员忙闲档（availability，受 A3 可见性约束：只回忙/闲块不回标题；个人读类无需授权）'},
    'plan:manage': {'label_zh': '管理企业会议协同', 'domain': 'plan', 'risk': 'medium', 'description': '管理企业会议协同：加/减参会人（invite）、代主人回复 RSVP（PLAN-ENT 企业双模，owner 隔离 + 企业角色两刀交集）'},
    # —— platform · 工作台（hasn.workbench.pending.scan：主脑聚合各应用未处理项，主动分诊派发，doc05）——
    'workbench:pending:read': {'label_zh': '扫描未处理项', 'domain': 'workbench', 'risk': 'low', 'description': '聚合主人名下各应用的未处理项（只读，供主脑分诊派发；简报发布 publish 无需 scope）'},
    # —— platform · 记忆（hasn.memory.save：分身把长期语义事实写入云端权威记忆，doc16 Phase C）——
    # 读类（search/recall/list）无需授权；写类 memory:write 出厂 Allow，owner 三态可覆盖、事后可改可删。
    'memory:write': {'label_zh': '记录记忆', 'domain': 'memory', 'risk': 'low', 'description': '把长期语义事实（偏好/事实/目标等）写入云端权威记忆（读类无需授权）'},
    # —— platform · 错误诊断（hasn.diag.* 云端工具，doc21）：平台运维分身跨 owner 读全量 issue/report + 改状态 ——
    # 特权口径（跨 owner），由 G1 平台特权门（doc18 U2）判定授予对象；出厂 Allow（无人值守运维，Ask 会死锁）。
    'diag:read:all': {'label_zh': '读平台错误全量', 'domain': 'diag', 'risk': 'medium', 'description': '跨 owner 读取平台错误 issue 列表/详情/occurrence/统计（平台运维特权，非普通分身）'},
    'diag:manage': {'label_zh': '管理平台错误 issue', 'domain': 'diag', 'risk': 'high', 'description': '改 issue 状态（investigating/resolved/skipped/wontfix）、挂 issue/PR 链接（平台运维特权，写审计留痕）'},
    # —— 历史默认词表（DEFAULT_AGENT_SCOPES）——展示兜底，无对应 cloud 工具亦不崩 ——
    'task:execute': {'label_zh': '执行任务', 'domain': 'task', 'risk': 'low', 'description': '历史默认任务执行权限'},
    'profile:read': {'label_zh': '读取资料', 'domain': 'profile', 'risk': 'low', 'description': '读取自身/主人公开资料'},
}

# ── G1 平台特权门（doc18 §4.1 · 实施/103 U2）──────────────────────────────
# 特权前缀整段排他：命中前缀的 scope 一律归特权，防漂移守卫据此强制。
# PLATFORM_SCOPE_CATALOG 是展示元数据注册表：可含**有意登记**的特权 scope 元数据（diag:* 等，
# 供运维分身工具可见时查 label/risk）——但凡命中特权前缀的键必须 ∈ PRIVILEGED_SCOPES；
# owner 级普通能力键**不得**误用特权前缀（未登记 = 漂移，会被 G1 错误隐身）。
# owner 级自查类能力须走其他前缀（diag 文档已预留 selfdiag:read），不得开豁免洞。
# 真正的「第四暴露面」隐藏在 build_scope_catalog 的 is_catalog_hidden（工具级 G1 过滤），与本词表内容无关。
PRIVILEGED_SCOPE_PREFIXES: tuple[str, ...] = ('diag:', 'ops:', 'platform:')

# 特权 scope 名单：已声明的特权 scope 全集。新增运维工具的 scope 必须先登记进来
# （守卫测试：注册表里凡 required_scopes 命中特权前缀的，必须 ∈ 本名单，防漏名单）。
# 一行一 scope，与 PLATFORM_SCOPE_CATALOG 同约定。
PRIVILEGED_SCOPES: frozenset[str] = frozenset({
    'diag:read:all',  # 运维分身读全平台错误聚合（hasn.diag.* 读类，21-可观测性 §8.2）
    'diag:manage',  # 运维分身处置错误 issue（hasn.diag.* 写类：update/resolve）
})


def is_privileged_scope(scope: str) -> bool:
    """scope 是否落在特权前缀（整段排他；通配授予值 `diag:*` 本身也命中）。"""
    return scope.startswith(PRIVILEGED_SCOPE_PREFIXES)


def is_valid_privileged_grant(grant: str) -> bool:
    """授予值格式校验：特权前缀 + 精确值或段尾整段通配（`ops:*`，`*` 仅限末段）。"""
    if not is_privileged_scope(grant):
        return False
    head, _, tail = grant.rpartition(':')
    return bool(head) and bool(tail) and ('*' not in head) and (tail == '*' or '*' not in tail)


def grant_matches_scope(grant: str, scope: str) -> bool:
    """单条授予值是否命中 scope：精确命中 ∨ 通配前缀命中（`ops:*` 覆盖 `ops:` 整棵子树）。"""
    if grant == scope:
        return True
    if grant.endswith(':*') and len(grant) > 2:
        return scope.startswith(grant[:-1])
    return False


def privileged_scopes_satisfied(needed: frozenset[str] | set[str], granted: frozenset[str] | set[str]) -> bool:
    """needed ⊆ granted，按「精确 ∨ 通配」展开（doc18 §4.1 判定）。"""
    return all(any(grant_matches_scope(grant, scope) for grant in granted) for scope in needed)
