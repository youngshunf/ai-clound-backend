from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAgentsSchemaBase(SchemaBase):
    """HASN Agent 基础模型"""

    hasn_id: str = Field(description='HASN Agent 唯一标识（格式: a_{uuid}）')
    star_id: str = Field(description='Agent 唤星号（如: 100001#star）')
    owner_id: str = Field(description='所属 Human 的 hasn_id')
    display_name: str = Field(description='Agent 显示名（支持中文，对外展示）')
    agent_name: str = Field(description='Agent 标识名')
    description: str | None = Field(None, description='Agent 描述')
    avatar: str | None = Field(None, description='头像（与 sys_user.avatar 对齐）')
    type: str = Field(
        description='Agent 类型 (desktop:桌面端:blue/mobile:手机端:green/cloud:云端:purple/web:网页端:orange)'
    )
    runtime_location: str = Field(default='local', description='运行位置 (local:本地:blue/cloud:云端:green)')
    role: str = Field(description='Agent 角色 (primary:主要:blue/specialist:专家:green/service:服务:orange)')
    node_id: str | None = Field(None, description='Agent 驻留节点 ID（设备指纹派生）')
    home_client_id: int | None = Field(None, description='本地 Agent 归属客户端 ID')
    template_id: str | None = Field(None, description='Agent 模板 ID')
    template_version: str | None = Field(None, description='Agent 模板版本（创建时快照）')
    skills: dict[str, Any] | list[Any] | None = Field(None, description='Agent 技能配置 JSON')
    soul_md: str | None = Field(None, description='Agent SOUL.md 内容')
    agents_md: str | None = Field(None, description='Agent AGENTS.md 内容')
    user_md: str | None = Field(None, description='Agent USER.md 内容')
    memory_md: str | None = Field(None, description='Agent MEMORY.md 内容（自我演化记忆）')
    profile_source: str = Field(default='cloud', description='Profile 来源')
    profile_revision: int = Field(default=1, description='Agent Profile 修订号')
    api_key_hash: str = Field(description='API Key 的 SHA256 哈希')
    status: str = Field(description='状态 (active:活跃:green/disabled:已停用:orange/revoked:已吊销:red)')
    created_via: str = Field(description='创建来源 (guardian:Guardian注册:blue/client:客户端创建:green)')
    social_enabled: bool = Field(default=False, description='是否对外开启社交可见')
    binding_node_id: str | None = Field(None, description='Agent 当前绑定的 node ID')
    binding_status: str = Field(default='unbound', description='binding 状态 (unbound/binding/bound/failed)')
    binding_updated_at: int | None = Field(None, description='binding 状态更新时间（Unix 秒）')
    online_status: str = Field(default='offline', description='在线状态 (offline:离线/online:在线)')
    last_heartbeat_at: datetime | None = Field(None, description='最后心跳时间（用于超时检测）')


class CreateHasnAgentsParam(HasnAgentsSchemaBase):
    """创建HASN Agent 参数"""


class UpdateHasnAgentsParam(HasnAgentsSchemaBase):
    """更新HASN Agent 参数"""


class DeleteHasnAgentsParam(SchemaBase):
    """删除HASN Agent 参数"""

    pks: list[int] = Field(description='HASN Agent  ID 列表')


class GetHasnAgentsDetail(HasnAgentsSchemaBase):
    """HASN Agent 详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None


class AgentSnapshot(SchemaBase):
    """云端 Agent Profile 快照，本地 hasn-node 以此为 Profile 事实源。"""

    hasn_id: str = Field(description='HASN Agent ID')
    star_id: str = Field(description='Agent 唤星号')
    owner_id: str = Field(description='所属 Human hasn_id')
    agent_name: str = Field(description='Agent 英文/目录标识')
    display_name: str = Field(description='Agent 显示名')
    description: str | None = Field(None, description='Agent 简介')
    avatar: str | None = Field(None, description='Agent 头像')
    type: str = Field(default='desktop', description='Agent 类型')
    runtime_location: str = Field(default='local', description='运行位置 (local:本地:blue/cloud:云端:green)')
    role: str = Field(default='specialist', description='Agent 角色')
    profession: str | None = Field(None, description='领域专家头衔（如「金融专家」）')
    builtin_agent_key: str | None = Field(
        None, description='内置 agent 标识 slug（如 daily_briefing/growth/creator）；用户自建分身为空'
    )
    node_id: str | None = Field(None, description='驻留节点 ID 摘要')
    capabilities: dict[str, Any] | list[Any] | None = Field(None, description='能力摘要')
    capability_set_id: str | None = Field(None, description='Agent 能力集 ID')
    persona_ref: str | None = Field(None, description='Agent persona 引用')
    tags: list[str] = Field(default_factory=list, description='Agent 标签数组')
    template_id: str | None = Field(None, description='模板 ID')
    template_version: str | None = Field(None, description='模板版本（创建时快照）')
    skills: dict[str, Any] | list[Any] | None = Field(None, description='技能配置')
    skill_display: dict[str, Any] | None = Field(
        None,
        description=(
            '技能显示层元数据：{skill_id: {name, description}} 映射，从 marketplace/个人技能目录'
            '批量解析下发（skills 本身只是 skill_id slug 清单，无友好名/描述）。'
            'daemon 据此把命令浮层技能项显示为真友好名+描述，而非裸 slug。附加字段，可空。'
        ),
    )
    soul_md: str | None = Field(None, description='SOUL.md 内容')
    agents_md: str | None = Field(None, description='AGENTS.md 内容')
    user_md: str | None = Field(None, description='USER.md 内容')
    memory_md: str | None = Field(None, description='MEMORY.md 内容（自我演化记忆）')
    profile_revision: int = Field(default=1, description='Profile 修订号')
    status: str = Field(default='active', description='Agent 状态/生命周期 (active/disabled/revoked/archived/deleted)')
    social_enabled: bool = Field(default=False, description='是否对外开启社交可见')
    binding_node_id: str | None = Field(None, description='Agent 当前绑定的 node ID')
    binding_status: str = Field(default='unbound', description='binding 状态 (unbound/binding/bound/failed)')
    binding_updated_at: int | None = Field(None, description='binding 状态更新时间（Unix 秒）')
    online_status: str = Field(default='offline', description='在线状态 (offline:离线/online:在线)')
    last_heartbeat_at: datetime | None = Field(None, description='最后心跳时间（用于超时检测）')
    updated_time: datetime | None = Field(None, description='更新时间')


class CloudCreateAgentRequest(SchemaBase):
    """hasn-node/WebUI 发起的云端优先 Agent 创建请求。"""

    owner_id: str = Field(description='Owner HASN ID')
    template_id: str | None = Field(None, description='模板 ID；空表示自定义')
    agent_name: str | None = Field(None, description='Agent 英文/目录标识；空则云端按显示名/模板生成')
    display_name: str = Field(description='Agent 显示名（像人一样的名字，全局唯一）')
    display_name_candidates: list[str] | None = Field(
        None, description='候选人名池（来自模板 name_pool）；display_name 撞名时云端按序挑首个空闲'
    )
    profession: str | None = Field(None, description='领域专家头衔（如「金融专家」，来自模板 name）')
    description: str | None = Field(None, description='Agent 简介')
    avatar: str | None = Field(None, description='Agent 头像 URL')
    skills: dict[str, Any] | list[Any] | None = Field(None, description='技能配置')
    soul_md: str | None = Field(None, description='SOUL.md 内容')
    agents_md: str | None = Field(None, description='AGENTS.md 内容')
    user_md: str | None = Field(None, description='USER.md 内容')
    memory_md: str | None = Field(None, description='MEMORY.md 内容（自我演化记忆）；空则取模板种子')
    runtime_type: str | None = Field(None, description='期望本地绑定 Runtime 类型')
    runtime_location: str = Field(
        default='local',
        description='运行位置 (local:本地非沙箱可访问授权目录/cloud:云端 Docker 沙箱)；默认 local',
    )
    node_id: str | None = Field(None, description='创建发起节点 ID')
    agent_type: str = Field(default='desktop', description='Agent 类型')
    role: str = Field(default='specialist', description='Agent 角色')
    capabilities: dict[str, Any] | list[Any] | None = Field(None, description='能力摘要')


class CheckDisplayNameRequest(SchemaBase):
    """创建分身前查人名是否可用（display_name 全局唯一）。"""

    display_name: str = Field(description='待校验的人名')
    candidates: list[str] | None = Field(None, description='候选人名池；撞名时据此给建议名')


class CheckDisplayNameResponse(SchemaBase):
    """人名查重结果。"""

    available: bool = Field(description='是否可用（全局未被占用）')
    suggestion: str | None = Field(None, description='撞名时的建议可用人名；可用时为空')


class AgentTokenInfo(SchemaBase):
    """Agent JWT 信息"""

    access_token: str = Field(description='Agent JWT')
    # 已退役：JWT 不再携带 scopes claim、授权只看三态（实施102 S0）。恒空列表，
    # 仅为兼容旧 daemon 的必填反序列化字段保留（daemon Rust 侧已改 serde default）。
    scopes: list[str] = Field(default_factory=list, description='已退役占位（恒空），授权走三态 capability_modes')


class CloudCreateAgentResponse(SchemaBase):
    agent: AgentSnapshot = Field(description='云端 Agent 快照')
    agent_key: str | None = Field(None, description='新建 Agent Key，仅创建时返回')
    agent_token: AgentTokenInfo | None = Field(None, description='Agent JWT，仅创建时返回')
    already_exists: bool = Field(default=False, description='是否幂等命中已有 Agent')


class UpdateAgentProfileRequest(SchemaBase):
    """daemon 发起的部分字段更新请求（云端为权威源）。

    所有字段都是 partial：未传递的字段保持云端现值。星号字段直接落库 hasn_agents 表，
    daemon 据返回的 AgentSnapshot 回写本地镜像。
    """

    display_name: str | None = Field(None, min_length=1, max_length=80, description='Agent 显示名')
    description: str | None = Field(None, max_length=280, description='Agent 简介')
    avatar: str | None = Field(None, max_length=500, description='Agent 头像 URL')
    role: str | None = Field(None, min_length=1, max_length=64, description='Agent 角色')
    profession: str | None = Field(None, max_length=60, description='领域专家头衔（如「金融专家」）')
    star_id: str | None = Field(None, min_length=1, max_length=40, description='Agent 唤星号（同表唯一）')
    tags: list[str] | None = Field(None, description='Agent 标签数组（覆盖式更新）')
    capability_set_id: str | None = Field(None, max_length=80, description='Agent 能力集 ID')
    persona_ref: str | None = Field(None, max_length=120, description='Agent persona 引用')
    status: str | None = Field(
        None,
        description='Agent 状态/生命周期 (active/disabled/revoked/archived/deleted)',
    )
    # 记忆三段（doc10 PUT 记忆 tab 编辑保存写云端权威）。owner 在「记忆」tab 编辑
    # 核心人设 / 主人档案 / 分身笔记 → daemon PATCH 上行 → 云端落库为权威源 →
    # 回 AgentSnapshot 给 daemon 镜像，profile_revision 自增触发 runtime 重拉。
    # partial 语义：键传入即写（含空串=清空该段），未传则保留云端现值。
    soul_md: str | None = Field(None, description='SOUL.md 内容（核心人设）')
    user_md: str | None = Field(None, description='USER.md 内容（主人档案）')
    memory_md: str | None = Field(None, description='MEMORY.md 内容（分身自我演化记忆/笔记）')


class UpdateAgentProfileResponse(SchemaBase):
    agent: AgentSnapshot = Field(description='更新后的 Agent 快照（云端权威）')


class AgentRuntimeModels(SchemaBase):
    """per-agent 4 槽模型选择（None=跟随主模型，runtime 不写对应键即回退 auto）。"""

    main: str | None = Field(None, max_length=120, description='主模型 model.default（强模型；空=平台默认）')
    fast: str | None = Field(
        None, max_length=120, description='快速模型，扇出到廉价文本辅助任务 auxiliary.*（空=跟随主模型）'
    )
    vision: str | None = Field(None, max_length=120, description='视觉模型 auxiliary.vision（空=跟随主模型）')
    delegation: str | None = Field(None, max_length=120, description='子分身模型 delegation.model（空=跟随主模型）')


class AgentRuntimeConfig(SchemaBase):
    """per-agent hermes runtime 原生配置（云端权威，全部可空=跟随默认）。"""

    models: AgentRuntimeModels = Field(default_factory=AgentRuntimeModels, description='4 槽模型选择')
    working_directory: str | None = Field(
        None, max_length=500, description='agent 工作目录 TERMINAL_CWD（绝对路径；空=默认隔离工作区）'
    )
    max_turns: int | None = Field(None, ge=1, le=1000, description='单任务最大执行轮数（空=默认 200）')
    gateway_timeout: int | None = Field(
        None, ge=30, le=7200, description='运行时网关无活动超时秒（空=默认 600；区别于权限页审批超时）'
    )
    memory_enabled: bool | None = Field(None, description='长期记忆系统开关（空=默认开）')
    user_profile_enabled: bool | None = Field(None, description='主人画像注入开关（空=默认开）')
    timezone: str | None = Field(None, max_length=64, description='agent 执行时区（空=默认 Asia/Shanghai）')
    a2a_max_turns: int | None = Field(
        None,
        ge=1,
        le=100,
        description='A2A 连续自动往返硬上限（分身↔分身互发到此轮数自动暂停并通知主人续聊；空=默认 10）',
    )
    restrict_file_access_to_workspace: bool | None = Field(
        None,
        description=(
            '是否把分身本地文件操作限定在工作目录内。空/False=默认放开（分身可读取工作区外文件、'
            '便于整理外部目录）；True=收紧到仅工作区内。daemon 组装本地 MCP key 时按此下发给 '
            'path_guard，管住 asset.upload/film/publish/marketplace 等收文件的本地工具。'
        ),
    )


class GetAgentRuntimeConfigResponse(SchemaBase):
    """读取/更新 Agent 运行时配置出参（未设项为 None）。"""

    config: AgentRuntimeConfig = Field(description='当前 runtime 配置')


class UpdateAgentRuntimeConfigRequest(AgentRuntimeConfig):
    """更新 Agent 运行时配置入参（覆盖式：webui 取当前值再整体提交）。"""


class AgentSkillInstallRequest(SchemaBase):
    """daemon 发起的「为 Agent 装配技能」请求（云端权威）。

    skill_id 为命名空间化资源 ID（{namespace}/{slug}）。云端校验技能已 published/public 后，
    把 skill_id 并入 hasn_agents.skills 清单（list[str] 保序去重）、bump profile_revision，
    返回最新 AgentSnapshot。version 仅记录意图，物化由 daemon/runtime 在 re-provision 时下载对应包。
    """

    skill_id: str = Field(min_length=1, max_length=255, description='技能资源 ID（{namespace}/{slug}）')
    version: str | None = Field(None, max_length=50, description='期望版本（可选，缺省取最新）')


class AgentSkillUninstallRequest(SchemaBase):
    """daemon 发起的「卸载 Agent 技能」请求（云端权威）。"""

    skill_id: str = Field(min_length=1, max_length=255, description='技能资源 ID（{namespace}/{slug}）')


class AgentBundleInstallRequest(SchemaBase):
    """daemon 发起的「为 Agent 安装技能包 skill_pack」请求（云端权威，实施/91 B2.5）。

    package_id 为 skill_pack 模板 ID（{namespace}/{slug}）。云端展开成员技能并入
    hasn_agents.skills、记录包引用进 hasn_agents.skill_bundles、bump profile_revision，
    返回 bundle 快照（含 hermes_yaml / 成员 skill_ids）供 daemon 回填本地 cache + provision 物化。
    """

    package_id: str = Field(min_length=1, max_length=255, description='技能包模板 ID（{namespace}/{slug}）')
    version: str | None = Field(None, max_length=50, description='期望版本（可选，缺省取最新）')


class AgentSyncRequest(SchemaBase):
    owner_id: str = Field(description='Owner HASN ID')
    after_revision: int | None = Field(None, ge=0, description='仅返回大于该 Profile revision 的 Agent')
    include_disabled: bool = Field(default=True, description='是否返回停用/删除态 Agent')


class AgentSyncResponse(SchemaBase):
    owner_id: str = Field(description='Owner HASN ID')
    server_revision: int = Field(ge=0, description='当前最大 Profile revision')
    agents: list[AgentSnapshot] = Field(default_factory=list, description='Agent 快照列表')
    common_skills_revision: str = Field(
        default='0',
        description='公共技能集合修订号（全局，daemon 据此变化触发全量活跃绑定 re-provision）',
    )
    platform_config_revision: str = Field(
        default='0',
        description='平台默认配置修订号（节点媒体模型 + agent 运行时四槽默认；daemon 据此变化拉取并应用 PDC）',
    )


class UpdateAgentBindingRequest(SchemaBase):
    """daemon 发起的 binding 状态更新请求。"""

    binding_node_id: str = Field(description='绑定的 node ID')
    binding_status: str = Field(description='binding 状态 (unbound/binding/bound/failed)')


class AgentHeartbeatRequest(SchemaBase):
    """daemon 发起的 agent 心跳上报请求。"""

    node_id: str = Field(description='当前 node ID')
    online_status: str = Field(description='在线状态 (online/offline)')
    health_status: str | None = Field(None, description='健康状态 (ok/degraded/error)')
    last_heartbeat_at: int = Field(description='最后心跳时间（Unix 秒）')


class AgentHeartbeatResponse(SchemaBase):
    """心跳上报响应。"""

    success: bool = Field(description='是否成功')


class AgentProfileResponse(SchemaBase):
    """Agent scope 直连拉取的 Profile（Runtime 据此物化为本地文件 + 下载技能）。

    身份恒取自 agent JWT，不读 body；Runtime 用 agent JWT 调
    GET /api/v1/hasn/agent/profile 获取自己的 Profile。
    """

    hasn_id: str = Field(description='HASN Agent ID')
    display_name: str = Field(description='Agent 显示名')
    runtime_location: str = Field(default='local', description='运行位置 (local:本地:blue/cloud:云端:green)')
    soul_md: str | None = Field(None, description='SOUL.md 内容')
    agents_md: str | None = Field(None, description='AGENTS.md 内容')
    user_md: str | None = Field(None, description='USER.md 内容（owner 记忆下发）')
    memory_md: str | None = Field(None, description='MEMORY.md 内容（自我演化记忆）')
    skills: list[str] = Field(default_factory=list, description='技能 skill_id 清单（已叠加公共技能）')
    common_skill_ids: list[str] = Field(
        default_factory=list,
        description='公共技能 skill_id 清单（skills 的子集，hermes 据此分流公共→共享目录；doc11 §5.2）',
    )
    skill_content_hashes: dict[str, str] = Field(
        default_factory=dict,
        description='per-skill 内容指纹映射 {skill_id: 指纹}，指纹=COALESCE(content_hash,file_hash,version)；'
        'Runtime 据此只重下指纹变化的技能（doc14 §C4）。市场无版本行的技能不出现，Runtime 回落为总是重下',
    )
    skill_bundles: list[dict] = Field(
        default_factory=list,
        description='已安装技能包清单 [{bundle_slug, command_key, hermes_yaml}]（Runtime 据此物化 skill-bundles/*.yaml）',
    )
    template_id: str | None = Field(None, description='模板 ID')
    template_version: str | None = Field(None, description='模板版本')
    profile_revision: int = Field(default=1, description='Profile 修订号（跨端同步信标）')
    common_skills_revision: str = Field(
        default='0',
        description='公共技能集合修订号（成员或内容版本变化即变，Runtime 据此重拉公共技能）',
    )
    installed_skills_revision: str = Field(
        default='0',
        description='Agent 自装技能内容修订号（自装技能内容升级即变，Runtime 据此重拉已装技能；doc14 §B4）',
    )
    runtime_config: dict | None = Field(
        None,
        description='hermes runtime 原生配置（4 槽模型/工作目录/knobs；Runtime 据此写 config.yaml/.env，空=全默认）',
    )


class RuntimeRunRequest(SchemaBase):
    """云端 runtime 派发请求（Agent JWT，仅 runtime_location=cloud 分身）。

    daemon 把它本地为 local 分身组装的 /v1/runs 派发信封（input/message/stream/
    dispatch_id/instructions/tools/...）整体放进 payload，并携带其 binding metadata
    里的 runtime_profile_id；云端据 profile_id 经 sidecar 控制面拿上游 gateway 端点，
    直连 POST /v1/runs 启动 run，并把 SSE 事件逐帧中继回 daemon。
    """

    runtime_profile_id: str = Field(description='hermes 上游 profile_id（如 100001-assistant），由 daemon 携带')
    payload: dict[str, Any] = Field(description='/v1/runs 派发信封（daemon 组装，云端不重组）')
    trace_id: str | None = Field(None, description='链路追踪 ID（透传到 runtime header）')


class RuntimeRunCancelRequest(SchemaBase):
    """取消进行中的云端 run。"""

    runtime_profile_id: str = Field(description='hermes 上游 profile_id')
    trace_id: str | None = Field(None, description='链路追踪 ID')


class RuntimeRunCancelResponse(SchemaBase):
    run_id: str = Field(description='被取消的 run_id')
    cancelled: bool = Field(description='是否成功取消')


class RuntimeHealthResponse(SchemaBase):
    """云端分身运行时健康（供 daemon 判可达性，双形态 Runtime 设计 08/02 §81）。

    语义：云端 runtime 控制面可达 = `online=True`（对齐「关机后仍在线」——云端常驻、
    网关按需起，冷网关不判降级）；控制面不可达 = `online=False`（零 fake，如实报离线）。
    daemon 据此写本地 `binding_runtime_state`，让云端分身的可达性来自云端真实健康，
    而非本地 hermes 探活（本地对云端分身根本没有它的网关）。
    """

    online: bool = Field(description='云端 runtime 控制面是否可达（可服务该分身）')
    health: str = Field(description="健康度：'ok'（可达）/'offline'（不可达）")
    detail: str | None = Field(None, description='细节（gateway_running/gateway_idle/不可达原因等，仅供观测）')


class RuntimeSkillItem(SchemaBase):
    """云端分身运行时的单个技能条目（列表用，与本地 hermes sidecar list_skills 同形态）。"""

    skill_id: str = Field(description='技能 ID（profile skills/ 目录名）')
    name: str = Field(description='技能名称')
    description: str = Field(description='技能描述')
    enabled: bool = Field(description='是否启用（未在 config.yaml skills.disabled 中）')


class RuntimeSkillsListResponse(SchemaBase):
    """列出云端分身运行时技能（双形态 Runtime，设计 04：云端 Runtime 收敛到 RuntimeAdapter）。"""

    skills: list[RuntimeSkillItem] = Field(default_factory=list, description='技能列表（未 provision 则为空）')


class RuntimeSkillReadResponse(SchemaBase):
    """读取云端分身某个技能的正文（SKILL.md）。"""

    skill_id: str = Field(description='技能 ID')
    name: str = Field(description='技能名称')
    description: str = Field(description='技能描述')
    content: str = Field(description='SKILL.md 正文')
    enabled: bool = Field(description='是否启用')


class RuntimeSkillMutateRequest(SchemaBase):
    """启用/停用云端分身某技能的请求（Agent JWT，仅 runtime_location=cloud 分身）。"""

    runtime_profile_id: str = Field(description='hermes 上游 profile_id，由 daemon 携带')
    trace_id: str | None = Field(None, description='链路追踪 ID')


class RuntimeSkillMutateResponse(SchemaBase):
    """启用/停用云端分身某技能的返回。"""

    skill_id: str = Field(description='技能 ID')
    enabled: bool = Field(description='操作后是否启用')
    success: bool = Field(description='是否达成目标状态（enable→enabled / disable→disabled）')


class AgentProfileRevisionResponse(SchemaBase):
    """轻量轮询：仅返回 Profile 修订号 + 公共技能修订号 + 自装技能内容修订号。"""

    profile_revision: int = Field(default=1, description='Profile 修订号')
    common_skills_revision: str = Field(default='0', description='公共技能集合修订号')
    installed_skills_revision: str = Field(default='0', description='Agent 自装技能内容修订号（doc14 §B4）')


# [ADR-15 收编兼容 re-export] Owner 记忆 DTO 已迁入 `app/hasn_memory.schema.owner_memory`。
# 保持既有 importer（本文件 + agent 侧 hasn_agent_profile.py）兼容；新代码请直接从 hasn_memory 导入。
from backend.app.hasn_memory.schema.owner_memory import (  # noqa: E402
    MemoryContributeRequest as MemoryContributeRequest,
)
from backend.app.hasn_memory.schema.owner_memory import (
    MemoryContributeResponse as MemoryContributeResponse,
)
from backend.app.hasn_memory.schema.owner_memory import (
    OwnerMemoryContributionItem as OwnerMemoryContributionItem,
)
from backend.app.hasn_memory.schema.owner_memory import (
    OwnerMemoryContributionsResponse as OwnerMemoryContributionsResponse,
)
from backend.app.hasn_memory.schema.owner_memory import (
    OwnerMemoryResponse as OwnerMemoryResponse,
)
