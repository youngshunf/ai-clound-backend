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
    'image:generate': {'label_zh': '生成图片', 'domain': 'image', 'risk': 'medium', 'description': '直连唤星 new-api 图像 API 生成图片（消耗 owner 配额）'},
    'video:generate': {'label_zh': '生成视频', 'domain': 'video', 'risk': 'high', 'default_mode': 'ask', 'description': '直连唤星 new-api 视频 API（task 式异步：提交→轮询）生成视频，单价远高于图片、消耗 owner 配额，故独立授权档'},
    # —— platform · 资产（hasn.asset.create：分身把自己的内容上传成 hasn://asset）——
    'asset:create': {'label_zh': '上传媒体资产', 'domain': 'asset', 'risk': 'medium', 'description': '把分身的内容（SVG/图片/文件等）上传到私有桶并注册资产，供消息附件引用'},
    # —— platform · 工作会话（hasn.session.ask：分身在工作会话里主动向主人提问、挂起会话等答复）——
    'session:ask': {'label_zh': '向主人提问', 'domain': 'session', 'risk': 'low', 'description': '工作会话中需主人决策/补关键信息时挂起会话、投提问卡到主会话等主人答复（绝不替主人臆测）'},
    # —— platform · 规划（hasn.plan.* 云端 CRUD：分身代主人管理目标/计划/待办/日程/习惯，模块 19）——
    # 注：plan:schedule / plan:delegate 仍属本地 hasn-mcp 工具（schedule/reschedule/delegate 保留本地），
    # 其展示元数据在本地 scope 词表，不在此平台目录。
    'plan:write': {'label_zh': '编辑规划', 'domain': 'plan', 'risk': 'low', 'description': '代主人建/改/删目标、计划、待办、日程、习惯（读类无需授权）'},
    'plan:manage': {'label_zh': '管理企业会议协同', 'domain': 'plan', 'risk': 'medium', 'description': '管理企业会议协同：加/减参会人（invite）、代主人回复 RSVP（PLAN-ENT 企业双模，owner 隔离 + 企业角色两刀交集）'},
    # —— platform · 记忆（hasn.memory.save：分身把长期语义事实写入云端权威记忆，doc16 Phase C）——
    # 读类（search/recall/list）无需授权；写类 memory:write 出厂 Allow，owner 三态可覆盖、事后可改可删。
    'memory:write': {'label_zh': '记录记忆', 'domain': 'memory', 'risk': 'low', 'description': '把长期语义事实（偏好/事实/目标等）写入云端权威记忆（读类无需授权）'},
    # —— 历史默认词表（DEFAULT_AGENT_SCOPES）——展示兜底，无对应 cloud 工具亦不崩 ——
    'task:execute': {'label_zh': '执行任务', 'domain': 'task', 'risk': 'low', 'description': '历史默认任务执行权限'},
    'profile:read': {'label_zh': '读取资料', 'domain': 'profile', 'risk': 'low', 'description': '读取自身/主人公开资料'},
}
