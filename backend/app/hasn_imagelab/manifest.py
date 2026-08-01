"""唤星图像处理应用（imagelab / 图坊，自研本地引擎）AI-Native 内置 manifest。

设计事实源：
- docs/hasn-node设计文档/14-AI-Native应用平台/30-图坊/01-架构设计.md §5.4/§5.5
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）

图坊是 **Tool-First** 自研本地图像处理应用：把已跑通的图像处理流水线（ffmpeg 抽帧 + rembg ML 抠图 +
Pillow 光栅编辑 + scipy 形态学 + libwebp 动画组装）收编为一个自研本地引擎。主人在 /apps/imagelab
快速处理或批量处理图片；分身经 hasn.imagelab.* 本地 MCP 工具用「处理配方（Recipe）」编排完成复杂批量
图片处理；产物默认存用户本地路径，点「分享」才上传云端发好友/群。确定性像素操作走本地引擎（快、私有、零算力
成本），生成类操作复用平台既有 hasn.image.generate（new-api）。

⚠️ 方案 A（同 deck/designsystem/film/reel）：`tools[]` **置空数组**——`hasn.imagelab.*` 工具数据面在本地
（hasn-mcp built_in_imagelab_tools()，source=Local，经进程内 ImageLabBroker 直达本机 sidecar），
**不经云端 Runtime Gateway `_dispatch_tool`**。故不进 `tools[]`（自造 transport 会静默过
validate_manifest 变潜伏炸弹）；`capabilities[]` 只承载发现/权限元数据控制面记录。

⚠️ scope 与落地工具对齐（hasn-node `crates/hasn-mcp/src/imagelab.rs` `capability_scopes()` + `scopes.py`）：
读类（workspace.get/analyze/job.get/job.list）`imagelab:read`（出厂 Allow）；处理类（process/pipeline/animate/
enhance/recipe.save/list/get/import）`imagelab:process`（出厂 Allow）；写盘导出类（export，写本地输出目录+
登记产物，非读——不得挂 read）`imagelab:export`（出厂 Allow）；批处理、破坏性另存和生成类分别使用
`imagelab:batch`、`imagelab:destructive`、`imagelab:generate`（均出厂 Allow，owner 可覆盖 Ask/Deny）；
外发分享类（share，产物上云发好友/群）使用 `imagelab:share`（唯一出厂 Ask）。
七 scope 与 §5.4 工具表、scopes.py IMAGELAB_SCOPE_CATALOG 三方逐一对齐——单一事实源，禁再漂移。

⚠️ execution_mode：catalog 枚举（`cloud/embedded_desktop/local_tool`）取 **`local_tool`**
（本地工具驱动 + sidecar 本机执行 + 原生 webui UI，非 cloud 执行）。按需下载形态（设计 §3
`downloadable_local`：自研引擎 + ML 模型按需下载）属分发底座（复用 film 已建底座），其 package/storage
字段经 catalog config_json.engine 承载；本期与 film/reel 同以 `local_tool` + `install_policy='manual'` 注册。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

# 与既有 AI-Native 审计共表的字段集（同 deck/designsystem/film/reel）。
_AUDIT_FIELDS = [
    'trace_id',
    'workspace',
    'app_id',
    'agent_hasn_id',
    'owner_hasn_id',
    'session_uuid',
    'tool_id',
    'required_scopes',
    'decision',
]

_READ_SCOPE = 'imagelab:read'
_PROCESS_SCOPE = 'imagelab:process'
_BATCH_SCOPE = 'imagelab:batch'
_DESTRUCTIVE_SCOPE = 'imagelab:destructive'
_GENERATE_SCOPE = 'imagelab:generate'
_EXPORT_SCOPE = 'imagelab:export'
_SHARE_SCOPE = 'imagelab:share'


def _allow_cap(
    *,
    name: str,
    title: str,
    description: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    scope: str,
) -> dict:
    """出厂 Allow 类能力（除外发分享外的读取、处理、生成、批量与写盘能力）。

    操作默认不覆盖原图、产物只落本地、可回滚；owner 可经 capability_modes 三态覆盖。
    """
    return {
        'capability_id': f'imagelab.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'imagelab.{name}',
        'mcp_name': f'hasn.imagelab.{name}',
        'required_scopes': [scope],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': 'low',
        'human_confirmation': {'required': False},
        'result_writeback': ['audit', 'agent_message'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': ['imagelab', 'image', 'photo'],
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


def _ask_cap(
    *,
    name: str,
    title: str,
    description: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    scope: str,
) -> dict:
    """出厂 Ask 类能力，仅用于外发分享；owner 可经 capability_modes 三态覆盖。
    """
    return {
        'capability_id': f'imagelab.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'imagelab.{name}',
        'mcp_name': f'hasn.imagelab.{name}',
        'required_scopes': [scope],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': 'medium',
        # 外发上云是唯一出厂 Ask；human_confirmation 仅承载 UI 提示，服务端仍执行 owner 三态门。
        'human_confirmation': {'required': True},
        'result_writeback': ['agent_message', 'audit'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': ['imagelab', 'image', 'photo'],
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


# 常用入参片段（配方 DSL / 图片引用 / 输入源）。
_IMAGE_REF = {'image': {'type': 'string', 'minLength': 1, 'description': '输入图（本地路径或 hasn://asset 引用）'}}
_SOURCE_REF = {
    'source_ref': {'type': 'string', 'minLength': 1, 'description': '输入源（图列表/目录/asset，批量处理的输入集）'}
}
_RECIPE = {
    'recipe': {
        'type': 'object',
        'description': '处理配方 DSL（有序 steps：每步 op + params + 可选 when；§5.2）',
    }
}

IMAGELAB_AI_NATIVE_MANIFEST: dict[str, Any] = {
    'app_id': 'imagelab',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'imagelab': '图像处理（去背景/裁剪/调色/拼图/动画/配方批量）'},
    'version': '1.0.0',
    'workspace_scope': ['personal'],
    'collaboration_mode': 'none',
    'project_aware': True,
    'project_required': True,
    'project_integration': 'project_required',
    'execution_mode': 'local_tool',
    'transport_mode': 'local',
    'notifications': {
        'emit': {
            'categories': ['app'],
            'card_message': True,
            'display_name': '图坊',
        }
    },
    # 资源描述符（doc31 §2，RC-P6）：平台项目内的图坊工作区
    # → hasn://imagelab/projects/{platform_project_id}，单入口 + ?project= 透传
    # （模式 sidebar 全页应用，非独立窗口）。entry_query·id 经 ?project 透传，图库据此定位项目。
    'resources': [
        {
            'resource_kind': 'imagelab.project',
            'uri_domain': 'imagelab/projects',  # → hasn://imagelab/projects/{platform_project_id}
            'open': {'mode': 'entry_query', 'entry_route': '/apps/imagelab', 'query_key': 'project'},
            'card': {'verb': '图像项目', 'action_label': '打开图坊'},
            'artifact_kind': 'resource',
        }
    ],
    'capabilities': [
        # —— 读类（imagelab:read，出厂 Allow）——
        _allow_cap(
            name='workspace.get',
            title='读取本机工作区状态',
            description='读取当前平台项目在本设备是否已授权且目录可写；只返回安全状态，不返回绝对路径。',
            properties={
                'platform_project_id': {
                    'type': 'string',
                    'format': 'uuid',
                    'minLength': 1,
                    'description': '当前工作会话挂靠的云端权威平台项目 UUID',
                }
            },
            required=['platform_project_id'],
            page_rank=9,
            scope=_READ_SCOPE,
        ),
        _allow_cap(
            name='analyze',
            title='读取与分析图片',
            description='读元信息/尺寸/格式/直方图/主色，检测主体/人脸/文字区域，看图说话（编排前置，无副作用）。',
            properties={**_IMAGE_REF},
            required=['image'],
            page_rank=10,
            scope=_READ_SCOPE,
        ),
        _allow_cap(
            name='job.get',
            title='查批处理任务',
            description='取一个批处理任务的进度与结果（分身跟进 job_id 直到 done/failed/canceled）。',
            properties={'job_id': {'type': 'string', 'minLength': 1, 'description': '批处理任务 id'}},
            required=['job_id'],
            page_rank=11,
            scope=_READ_SCOPE,
        ),
        _allow_cap(
            name='job.list',
            title='列批处理任务',
            description='列出当前主人的批处理任务（进行中/历史）。',
            properties={'limit': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 100}},
            required=[],
            page_rank=12,
            scope=_READ_SCOPE,
        ),
        # —— 非破坏性处理类（imagelab:process，出厂 Allow）——
        _allow_cap(
            name='process',
            title='单算子处理',
            description='单算子非破坏性处理（去背景/换背景/裁剪/缩放/调色/滤镜/格式/压缩/拼图/水印/文字…op+params）；'
            '默认不覆盖原图、产物只落本地。破坏性/生成类 op 一律拒（走 retouch/generate 专用工具）。',
            properties={
                **_IMAGE_REF,
                'op': {'type': 'string', 'minLength': 1, 'description': '算子名（如 remove_background/resize/crop）'},
                'params': {'type': ['object', 'null'], 'description': '算子参数'},
            },
            required=['image', 'op'],
            page_rank=20,
            scope=_PROCESS_SCOPE,
        ),
        _allow_cap(
            name='pipeline',
            title='跑一条配方（多步骤）',
            description='把一条处理配方（多步骤有序 steps）应用到单图/少量图；引擎顺序跑、中间产物接力，只落最终产物。'
            'steps 含破坏性 op → 并集升门按 imagelab:destructive 判（Ask）；generate 类 op 禁入配方（§5.2）。',
            properties={**_RECIPE, **_IMAGE_REF},
            required=['recipe', 'image'],
            page_rank=21,
            scope=_PROCESS_SCOPE,
        ),
        _allow_cap(
            name='animate',
            title='动画与序列帧',
            description='动画专项：视频→透明循环 WebP（birefnet 抠图 + 帧手术）/ 序列帧→动画（WebP/GIF/APNG）/ 帧手术'
            '（增删重排帧、改时长/循环、变速）。',
            properties={
                **_IMAGE_REF,
                'op': {'type': 'string', 'minLength': 1, 'description': '动画算子（如 video_to_webp/frames_to_anim）'},
                'params': {'type': ['object', 'null'], 'description': '动画算子参数'},
            },
            required=['image', 'op'],
            page_rank=22,
            scope=_PROCESS_SCOPE,
        ),
        _allow_cap(
            name='enhance',
            title='纯本地模型增强',
            description='纯本地模型增强（超分放大/老照片修复/降噪/本地上色）；凡走 new-api 的生成型增强'
            '（云上色/风格化）一律改走 hasn.imagelab.generate（单工具单 scope，静态门可判）。',
            properties={
                **_IMAGE_REF,
                'op': {'type': 'string', 'minLength': 1, 'description': '增强算子（如 upscale/restore/denoise）'},
                'params': {'type': ['object', 'null'], 'description': '增强算子参数'},
            },
            required=['image', 'op'],
            page_rank=23,
            scope=_PROCESS_SCOPE,
        ),
        _allow_cap(
            name='recipe.save',
            title='保存配方',
            description='把一段操作序列存成命名配方（复用/分享的载体，§5.2）。',
            properties={
                'name': {'type': 'string', 'minLength': 1, 'description': '配方名'},
                **_RECIPE,
            },
            required=['name', 'recipe'],
            page_rank=24,
            scope=_PROCESS_SCOPE,
        ),
        _allow_cap(
            name='recipe.list',
            title='列配方',
            description='列出内置模板 / 我的 / 团队共享配方。',
            properties={'limit': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 100}},
            required=[],
            page_rank=25,
            scope=_PROCESS_SCOPE,
        ),
        _allow_cap(
            name='recipe.get',
            title='取配方',
            description='取一条配方的完整步骤 DSL。',
            properties={'recipe_id': {'type': 'string', 'minLength': 1, 'description': '配方 id'}},
            required=['recipe_id'],
            page_rank=26,
            scope=_PROCESS_SCOPE,
        ),
        _allow_cap(
            name='import',
            title='导入图片进项目',
            description='从本地文件/asset/消息/URL/截图导入图片进项目。分身侧本地路径导入限「已授予范围」'
            '（work_dir ∪ 输出目录 ∪ 已登记导入源目录），范围外拒 path_not_granted（防分身零确认读任意本地图片）。',
            properties={
                'project_id': {'type': ['string', 'null'], 'description': '目标项目 id（不传则默认项目）'},
                'source': {'type': 'string', 'minLength': 1, 'description': '导入来源（local/asset/url/screenshot）'},
                'ref': {'type': 'string', 'minLength': 1, 'description': '来源引用（本地路径/asset uri/url）'},
            },
            required=['source', 'ref'],
            page_rank=27,
            scope=_PROCESS_SCOPE,
        ),
        # —— 写盘导出类（imagelab:export，出厂 Allow——写本地输出目录 + 登记产物，是写盘动作，scope 不挂 read）——
        _allow_cap(
            name='export',
            title='导出到本地输出目录',
            description='把结果写到用户本地输出目录 → 登记 imagelab_outputs（存 local_path，share_state=local_only，'
            '不上云；可打包 zip）。写盘动作，非读——scope 归 imagelab:export。',
            properties={
                **_IMAGE_REF,
                'project_id': {'type': ['string', 'null'], 'description': '归属项目 id'},
                'format': {'type': ['string', 'null'], 'description': '导出格式（webp/png/jpg/gif…）'},
                'pack_zip': {'type': ['boolean', 'null'], 'description': '是否打包 zip（批量产物）'},
            },
            required=['image'],
            page_rank=30,
            scope=_EXPORT_SCOPE,
        ),
        # —— 大批量类（imagelab:batch，出厂 Allow；提交即返 job_id 不阻塞）——
        _allow_cap(
            name='batch',
            title='配方批量处理',
            description='把一条配方批量应用到 N 张图/目录/asset 列表；提交即返 {job_id} 不阻塞'
            '（长任务经 job.get 轮询）。steps 含破坏性 op → 并集升门按 imagelab:destructive 判；'
            'generate 类 op 禁入配方（§5.2）。',
            properties={**_RECIPE, **_SOURCE_REF},
            required=['recipe', 'source_ref'],
            page_rank=40,
            scope=_BATCH_SCOPE,
        ),
        # —— 破坏性另存类（imagelab:destructive，出厂 Allow；owner 可覆盖 Ask/Deny）——
        _allow_cap(
            name='retouch',
            title='局部消除 / 去水印（破坏性）',
            description='局部消除/物体消除/路人消除/水印去除（inpaint，伪造/抹除像素，默认询问）；可对比原图、不覆盖原图。',
            properties={
                **_IMAGE_REF,
                'op': {'type': 'string', 'minLength': 1, 'description': '破坏性算子（如 inpaint/remove_object）'},
                'params': {'type': ['object', 'null'], 'description': '算子参数（如消除区域/掩膜）'},
            },
            required=['image', 'op'],
            page_rank=41,
            scope=_DESTRUCTIVE_SCOPE,
        ),
        # —— 生成类（imagelab:generate，出厂 Allow；桥接平台 hasn.image.generate 消耗配额）——
        _allow_cap(
            name='generate',
            title='生成式处理（桥接平台）',
            description='生成式填充/扩图/图生图/云增强 → 桥接平台 hasn.image.generate'
            '（MediaBroker/new-api，消耗积分）；图坊不自建生成模型，拿回结果路径后接续确定性处理。',
            properties={
                'prompt': {'type': 'string', 'minLength': 1, 'description': '生成提示词'},
                'image': {'type': ['string', 'null'], 'description': '输入图（图生图/生成式填充时；文生图可空）'},
                'op': {'type': ['string', 'null'], 'description': '生成算子（如 inpaint_fill/outpaint/img2img）'},
            },
            required=['prompt'],
            page_rank=42,
            scope=_GENERATE_SCOPE,
        ),
        # —— 外发分享类（imagelab:share，出厂 Ask——产物上云 + 发好友/群，外发动作须确认）——
        _ask_cap(
            name='share',
            title='分享产物到好友/群',
            description='把某个本地产物经 ensure_output_asset 上传私有桶 → hasn://asset → 发给指定好友/群'
            '（回填 asset_uri + share_state=shared + 追加 share_targets；对齐 route_message 门控）。',
            properties={
                'output_id': {'type': 'string', 'minLength': 1, 'description': '产物 id（imagelab_outputs）'},
                'targets': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'minItems': 1,
                    'description': '分享目标（好友/群的 hasn 号或会话 id）',
                },
            },
            required=['output_id', 'targets'],
            page_rank=43,
            scope=_SHARE_SCOPE,
        ),
    ],
    # 方案 A：本地工具不进 tools[]（走 hasn-mcp source=Local，bootstrap 发现）。
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}


def build_imagelab_app() -> App:
    """imagelab（图坊）App（local_tool / 内联路由 /apps/imagelab / 非自动挂载）。

    - ``install_policy='manual'``：图坊是自研本地引擎应用（图坊架构 §3.2/§5.5），按需装，不自动挂载到工作台
      （``default_mount=FALSE`` 由 install_policy 推导）。用户可见的启动入口随 webui + ``hasn_app_catalog``
      目录行落地（同 film/reel/designsystem 先例）。
      注册到 app_catalog_registry 是 ``validate_manifest`` 的硬前置（否则 workbench_app_not_found）。
    - ``collaboration_mode='none'`` / ``scope=('personal',)`` 必须与 manifest 对齐（validate 闸门）。
    - ``execution_mode='local_tool'`` / ``ui_kind=None``：原生 WebUI 页头壳六分区
      （图库/快速处理/产物/配方/批处理/设置），
      非 embedded sidecar、非 iframe。
    - ``entry_route='/apps/imagelab'``：路由统一在 /apps 前缀下。

    延迟导入 App 避免循环依赖。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='imagelab',
        name='图坊',
        icon='brand-imagelab',
        description='把图片处理的杂活交给分身——抠图换背景、裁剪调色、加水印、拼图压缩、做动画、超分修复，还能存成配方批量跑。',
        scope=('personal',),
        collaboration_mode='none',
        entry_route='/apps/imagelab',
        install_policy='manual',
        execution_mode='local_tool',
        ui_kind=None,
        project_aware=True,
        project_required=True,
    )
