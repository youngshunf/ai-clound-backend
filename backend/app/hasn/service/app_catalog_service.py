"""应用目录 / 权益领域服务（C1 数据层）。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md

职责：
- ``ensure_catalog_seeded``：从 ``app_catalog_registry`` 幂等播种 ``hasn_app_catalog``（迁移 M2）。
  **只插入缺失行，绝不回写已存在行的 display/价格**——这是「代码不覆盖运营改动」的关键
  （区别于 manifest 的 hash 自愈逻辑，见设计 §6.1）。
- ``sweep_expired_entitlements``：把 ``expires_at < now`` 的 active 权益置 expired（设计 §5.4 定时兜底）。

生成的 ``hasn_app_catalog_service`` / ``hasn_app_entitlement_service`` 负责 Admin CRUD；
本模块只承载播种与兜底这类领域逻辑，避免改动 codegen 产物。
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.billing.model import UserSubscription
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.app_catalog_registry import App, app_catalog_registry
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

# 默认应用标识（订阅档与 billing 同源，见 [[project_billing_newapi_authoritative_source]]）。
_DEFAULT_APP_CODE = 'huanxing'

# 订阅档高低序（设计 §5.4 枚举 free/pro/advanced/flagship）。未知档位保守按最低 free 处理。
_TIER_RANK: dict[str, int] = {'free': 0, 'pro': 1, 'advanced': 2, 'flagship': 3}

# 工作台排序（小在前）。未列出的 app 落到默认值之后。
_CATALOG_SORT_ORDER: dict[str, int] = {
    'knowledge': 10,
    'community': 20,
    'deck': 35,
    'publish': 40,
    'growth': 45,  # 获客（设计 §3.2 约 40，置于 publish 之后；default_mount=FALSE 由 install_policy=manual 推导）
    'creator': 50,  # 创作运营（置于 growth 之后；default_mount=FALSE 由 install_policy=manual 推导）
    'film': 55,  # 视频生成（源自 VideoClaw；default_mount=FALSE 由 install_policy=manual 推导）
    'reel': 57,  # 短视频合成（源自 MoneyPrinterTurbo，瘦引擎应用；default_mount=FALSE 由 install_policy=manual 推导）
    'copilot': 60,  # 会议副驾（local_tool 无 Agent 工具；default_mount=FALSE 由 install_policy=manual 推导）
    'plan': 65,  # 规划与目标管理（PIM；default_mount=FALSE 由 install_policy=manual 推导）
    'finance': 70,  # 金融数据（cloud 只读数据应用；default_mount=FALSE 由 install_policy=manual 推导）
    'quant': 75,  # 量化交易（cloud-brokered 量化工作台，模块 14 doc23；default_mount=FALSE 由 install_policy=manual 推导）
    'studio': 76,  # 统一视频引擎（cloud-brokered 视频工作台，模块 14 doc22；default_mount=FALSE 由 manual 推导）
}
_DEFAULT_SORT_ORDER = 100

# AppCollab（doc21 §4.3/§5.4）：应用默认承接的内置 agent 类型键 + 唤起分身注入的业务提示词模板。
# 类型键 = hub 内置模板的 ``builtin_key``（``builtin: true``）；daemon ``resolve_default_agent_for_app`` 按
# ``hasn_agents.builtin_agent_key == default_agent_type`` 取 owner 名下分身、命中即返回否则回退主脑。
# 同型键 = 一个分身默认服务多应用：
#   - ``content_operator``（内容运营官）：deck/designsystem/creator/film/community/publish 六应用；
#   - ``assistant``（全能助理）：knowledge/hasn_task 两应用；
#   - ``sales_advisor``（销售顾问）：growth；``meeting_copilot``：copilot；``planner``：plan。
# 未列出的应用 default_agent_type=NULL（回退主脑）、work_session_system_prompt=NULL（仅用本次指令）。
_CATALOG_AGENT_DEFAULTS: dict[str, tuple[str, str]] = {
    # 知识库 / 任务都交给「全能助理（assistant）」——通用执行型分身。
    'knowledge': (
        'assistant',
        '你是知识库应用的执行分身：帮主人整理、检索、问答知识库内容，沉淀可复用的知识资产；'
        '只调用 hasn.knowledge.* 工具，引用须可溯源，零 fake，失败如实报错。',
    ),
    'hasn_task': (
        'assistant',
        '你是任务应用的执行分身：把主人交办的事按计划执行、把结果带回并可追溯；'
        '只调用 hasn.task.* 工具，零 fake，失败如实报错。',
    ),
    # 社区 / 网页发布归「内容运营官（content_operator）」——与 deck/creator/film/designsystem 同一分身。
    'community': (
        'content_operator',
        '你是社区应用的执行分身：替主人在社区发现内容、发帖与互动、经营关注关系；'
        '只调用社区相关工具，对客可见内容须得体专业，零 fake，失败如实报错。',
    ),
    'publish': (
        'content_operator',
        '你是网页发布应用的执行分身：把主人或分身产出的网页/海报/演示发布成稳定分享链接并管理可见性；'
        '只调用 hasn.publish.* 工具，升级敏感可见性需主人确认，零 fake，失败如实报错。',
    ),
    # 获客用专属「销售顾问（sales_advisor）」分身——找线索、做跟进、促成交是独立专长。
    'growth': (
        'sales_advisor',
        '你是获客应用的执行分身：替主人找线索、做跟进、促成交，沉淀可复用的获客打法；'
        '只调用 hasn.growth.* 工具，合规先行、对外触达过主人确认，每一步对主人透明，零 fake，失败如实报错。',
    ),
    'deck': (
        'content_operator',
        '你是演示文稿应用的执行分身：把主人的诉求做成结构清晰、视觉专业的演示文稿，'
        '只调用 hasn.deck.* 工具就地生成与精修；产出对客可用的成品，零 fake，失败如实报错。',
    ),
    'designsystem': (
        'content_operator',
        '你是设计系统应用的执行分身：产出渲染目标无关的 token 契约 + 组件库，下游一律 var(--token) 消费；'
        '只调用 hasn.designsystem.* 工具，零 fake，失败如实报错。',
    ),
    'creator': (
        'content_operator',
        '你是内容运营应用的执行分身：围绕账号定位做选题、创作与发布编排，沉淀可复用打法；'
        '只调用 hasn.creator.* 工具，产出对客可用的成品，零 fake，失败如实报错。',
    ),
    # 视频生成（源自 VideoClaw）也归「内容运营官（content_operator）」——视频是内容运营的一种产出形态，
    # 不另起「视频分身」（AC-P6 福仔拍板复用 content_operator）。一个分身默认服务 deck/designsystem/creator/film 四应用。
    'film': (
        'content_operator',
        '你是视频生成应用的执行分身：把主人的创意做成完整的短视频，按脚本→角色设定→分镜→参考图→'
        '片段生成→合成的流水线推进；只调用 hasn.film.* 工具就地生成与精修；产出对客可用的成品，'
        '零 fake，失败如实报错。',
    ),
    # 短视频合成（源自 MoneyPrinterTurbo）也归「内容运营官（content_operator）」——它是创作运营的
    # 合成式视频能力提供方（doc19 §5.5）：项目/素材库/成品/审核发布全用 creator，reel 只出「合成」能力。
    # 一个分身默认服务 deck/designsystem/creator/film/reel 等多应用。
    'reel': (
        'content_operator',
        '你是短视频合成应用的执行分身：在创作运营的内容流水线里，把脚本/主题（取自内容项）配上素材'
        '（取自创作运营素材库，自带优先、不足才补库存）、配音与字幕，用 hasn.reel.* 工具本地合成出'
        '口播/带货/资讯类短视频；成片本地优先、重资产不自动上云，归属与发布走创作运营。'
        '只调用 hasn.reel.* 工具，文案与成片在确认点摊给主人，零 fake，失败如实报错。',
    ),
    # 会议副驾用专属「会议副驾」分身（hub 模板 meeting-copilot，builtin_key=meeting_copilot），
    # 非 content_operator——会议实时副驾是独立专长。
    'copilot': (
        'meeting_copilot',
        '你是会议副驾的执行分身：边听会议/通话的双方对话，边给关键要点、可追问的问题、待办与易错点；'
        '克制不刷屏、宁缺毋滥。会后按结构化纪要方法产出纪要落产物。只在本工作会话内工作，'
        '听不清就如实标注，零 fake、失败如实报错。',
    ),
    # 规划与目标管理用专属「私人参谋长」分身（hub 模板 planner，builtin_key=planner，PLAN-P4 落地）。
    # 一个分身既当参谋长（拆目标/排计划/简报复盘）又当执行秘书（捕获/排期/委托）。
    'plan': (
        'planner',
        '你是主人的私人参谋长 + 执行秘书：帮主人把模糊想法收敛成目标/关键结果，拆成可执行的计划与待办，'
        '合理排期到日历，每日给简报、定期做复盘；只调用 hasn.plan.* 工具就地管理主人的规划数据，'
        '尊重主人的最终决定权，零 fake、失败如实报错。',
    ),
    # 金融数据用专属「投研分析师（analyst）」分身（hub 模板 analyst，builtin_key=analyst，FIN-S8 落地）。
    # 行情/基本面/宏观全只读取数 → 分析师只查不动，给出有数据支撑的研判。
    'finance': (
        'analyst',
        '你是主人的投研分析师：用 hasn.finance.* 工具查 A股/港美股/基金/期货/债券/指数行情与宏观数据，'
        '为主人做有数据支撑的研判；所有数据仅供参考、不构成投资建议，引用须标注口径与日期，'
        '取不到就如实说，零 fake、失败如实报错。',
    ),
    # 量化用专属「量化交易官（quant_trader）」分身（hub 模板 quant_trader，builtin_key=quant_trader，QUANT-P10/P11 落地）。
    # 本期 P0–P5 只做回测研究（零资金风险）：写策略 → 跑回测 → 读绩效 → 迭代优化；实盘线 P6+ 受硬闸不开。
    'quant': (
        'quant_trader',
        '你是主人的量化交易官：用 hasn.quant.* 工具写量化策略、提交历史回测、读绩效报告并迭代优化；'
        '回测只花算力、不动钱，可大胆假设小心求证。所有绩效来自引擎真实回测、绝不臆造数字（零 fake）；'
        '回测表现不代表实盘收益，不构成投资建议；实盘部署/下单等动真钱动作须经主人审批，'
        '取不到/跑不通就如实报错，尊重主人最终决定权。',
    ),
    # 统一视频引擎（source OpenMontage，模块 14 doc22）也归「内容运营官（content_operator）」——视频是内容运营
    # 的一种产出形态（对齐 film/reel，不另起「视频分身」）。本期 P2 只铸目录，工具面随 P3 落地。
    'studio': (
        'content_operator',
        '你是主人的视频内容运营官：用 hasn.studio.* 管线与工具把创意做成完整视频，按脚本→分镜→配音→合成的'
        '流水线推进、迭代精修；提交渲染/出片、导出成片、分享发布等花算力或外发的动作须经主人审批。'
        '所有成片来自引擎真实渲染、绝不伪造产物（零 fake），取不到/跑不通就如实报错，尊重主人最终决定权。',
    ),
}


# 应用专属平台级配置默认骨架（catalog.config_json，FILMCFG-1）。
# 取代原 PDC node.film 通道：film 的 5 类模型 failover + 引擎包 manifest（内联）跟着应用走。
# 仅作首次播种默认（INSERT-only，不覆盖运营已填值）；空骨架运营在管理端 JSON 编辑器填。
# 未列出的应用 config_json 默认 {}（暂无平台级配置需求）。
_CATALOG_DEFAULT_CONFIG: dict[str, dict] = {
    # VideoClaw 视频引擎：5 类模型 failover + 引擎分发包 manifest 内联。
    # 出厂给出**完整骨架 + 示例模型名**（不是空 {} / 空数组）：管理端「编辑配置」弹框开箱即见
    # 结构，运营/主人**只改模型名**即可用，不必从零手写整份 JSON。示例名（gpt-5 / gpt-image-2 /
    # kling-1）是**占位模板**——务必换成各自 new-api 已开通的真实模型；视频尤其需先在 new-api
    # 开通视频渠道，否则分身调用会 honest 撞渠道错误（零 fake：daemon 如实报错，绝不伪造成功）。
    # 引擎 manifest（version + 按架构 packages{os-arch: {url, sha256, size}}）留空：dev 用 fork
    # 源码树即可跑，prod 由运营在对象存储托管引擎包后经管理端填。
    'film': {
        'models': {
            'llm': ['gpt-5'],
            'vlm': ['gpt-5'],
            'image_t2i': ['gpt-image-2'],
            'image_it2i': ['gpt-image-2'],
            'video': ['kling-1'],
        },
        'engine': {
            'version': '',
            'packages': {},
        },
    },
    # 短视频合成（reel，源自 MoneyPrinterTurbo，doc19 §6.4）：合成式管线，**只有 llm（文案/搜索词）
    # + tts（配音）+ stt（字幕，可选）三类模型，绝无 image/video 生成模型**（不烧视频 token）。
    # 出厂给出**完整骨架 + 示例值**（管理端「编辑配置」开箱即见结构），运营/主人改值即可：
    #   - models.llm 是 **failover 列表**（首个为主、其余兜底）：默认 agnes-2.0-flash 为主
    #     + deepseek-v4-pro / qwen3.7-plus 兜底（均为 new-api 已开通的真实文案模型，reel **无需开通
    #     视频渠道**）。agnes 单渠道偶发 503/超时（vllm 自建上游 ~10s/次），有兜底则自动切换不硬失败；
    #     运营可在管理端「编辑配置」换主/增删兜底；models.stt 默认空（字幕默认走 edge 时间戳，
    #     subtitle_provider=whisper 时才下 whisper 模型）。
    #   - tts 默认 edge（免费微软），可切 platform（走 hasn.voice.synthesize / owner 配额）。
    #   - material.platform_keys 是平台统一兜底素材 key（M2）：**留空占位，运营在管理端填**，绝不硬编码
    #     真实 key；owner 可在应用内自填（多 key 轮换避限流，doc19 §6.3）。
    #   - engine.bundled_deps=['ffmpeg','imagemagick']（reel 特有本地合成依赖，M3/N5）；engine manifest
    #     （version + 按架构 packages）留空：dev 用 fork 源码树即可跑，prod 由运营经管理端/FILMPUB 填。
    'reel': {
        'models': {
            'llm': ['agnes-2.0-flash', 'deepseek-v4-pro', 'qwen3.7-plus'],
            'tts': ['edge'],
            'stt': [],
        },
        'tts': {
            'tts_provider': 'edge',
            'voice_name': 'zh-CN-XiaoxiaoNeural',
            'voice_rate': 1.0,
        },
        'subtitle': {
            'subtitle_provider': 'edge',
            'whisper_model_size': 'large-v3',
        },
        'material': {
            'video_source': 'pexels',
            'platform_keys': {
                'pexels': [],
                'pixabay': [],
            },
        },
        'video': {
            'video_aspect': '9:16',
            'video_clip_duration': 5,
            'subtitle_style': {
                'font_name': '',
                'font_size': 60,
                'stroke_color': '#000000',
                'text_color': '#FFFFFF',
                'position': 'bottom',
            },
            'bgm': {
                'bgm_type': 'random',
                'bgm_volume': 0.2,
            },
            'video_count': 1,
        },
        'engine': {
            'version': '',
            'packages': {},
            'bundled_deps': ['ffmpeg', 'imagemagick'],
        },
    },
}


def _catalog_row_from_app(app: App) -> dict:
    """把 App 映射为 catalog 行的默认值（迁移期单一来源）。

    新增字段（source/status/商业化…）取保守默认：全部内置、已上架、免费。
    """
    return {
        'app_id': app.id,
        'name': app.name,
        'icon': app.icon,
        'icon_asset_uri': None,
        'description': app.description,
        'source': 'builtin',
        'status': 'published',
        'execution_mode': app.execution_mode,
        'scope': list(app.scope),
        'collaboration_mode': app.collaboration_mode,
        'entry_route': app.entry_route,
        'sort_order': _CATALOG_SORT_ORDER.get(app.id, _DEFAULT_SORT_ORDER),
        'default_mount': app.install_policy == 'auto',
        'requires_role': app.requires_role,
        # 商业化默认：保持现状全免费（迁移 M2 不变量）。
        'access_type': 'free',
        'min_tier': None,
        'price_amount': None,
        'price_unit': 'cny',
        'billing_cycle': 'once',
        'trial_days': 0,
        'sku_ref': None,
        # 现有 builtin（knowledge/community/deck）都有对应 code manifest。
        'manifest_present': True,
        # AppCollab：默认承接分身类型 + 业务提示词（doc21 §4.3）。未列出的应用留 None。
        'default_agent_type': _CATALOG_AGENT_DEFAULTS.get(app.id, (None, None))[0],
        'work_session_system_prompt': _CATALOG_AGENT_DEFAULTS.get(app.id, (None, None))[1],
        # 应用专属平台级配置默认骨架（FILMCFG-1）。仅首次播种，运营改值经管理端落 config_json。
        'config_json': dict(_CATALOG_DEFAULT_CONFIG.get(app.id, {})),
    }


async def ensure_catalog_seeded(db: AsyncSession) -> int:
    """幂等播种 catalog：仅插入缺失的 app_id 行，已存在行原样保留。

    返回新插入的行数。可在部署 reconcile / 测试夹具中调用。
    """
    existing = set((await db.execute(sa.select(HasnAppCatalog.app_id))).scalars().all())
    inserted = 0
    for app in app_catalog_registry.list():
        if app.id in existing:
            continue
        db.add(HasnAppCatalog(**_catalog_row_from_app(app)))
        inserted += 1
    if inserted:
        await db.flush()
    return inserted


async def get_all_app_configs(db: AsyncSession) -> dict[str, dict]:
    """聚合各应用平台级配置（``catalog.config_json``）成 ``{app_id: config_json}``（FILMCFG-1）。

    供 ``platform_default_config_service.get_effective_config`` 拼进 platform-config 下发响应的
    ``app_configs`` 字段——daemon 由此从 ``app_configs.<app_id>`` 读应用配置（取代原 PDC node.film）。
    仅返回 config_json 非空（``{}`` 视为未配置）的应用，避免下发噪音。
    """
    rows = (await db.execute(sa.select(HasnAppCatalog.app_id, HasnAppCatalog.config_json))).all()
    return {app_id: cfg for app_id, cfg in rows if cfg}


def merge_engine_package(
    config_json: dict | None,
    *,
    os_arch: str,
    version: str,
    key: str,
    url: str,
    sha256: str,
    size: int,
) -> dict:
    """把一个引擎分发包条目并入 ``config_json.engine``，返回**新** dict（不原地改）。

    语义（与 ``scripts/build/package-film-engine.sh`` 的 manifest 累积一致，FILMPUB）：
    - 同版本：累积/覆盖对应 ``os_arch`` 包条目（多架构分别发布合进同一 manifest）。
    - 版本跃迁（已有非空 version 且与本次不同）：旧 packages 全清空重来——旧架构包不属于新版本，
      留着会让 daemon 下到版本不匹配的包。
    - ``version`` 为空字符串视为「未发布过」，直接采用本次 version。
    """
    existing = dict(config_json or {})
    engine = dict(existing.get('engine') or {})
    prev_version = (engine.get('version') or '').strip()
    packages = dict(engine.get('packages') or {})
    if prev_version and prev_version != version:
        packages = {}
    packages[os_arch] = {'key': key, 'url': url, 'sha256': sha256, 'size': size}
    engine['version'] = version
    engine['packages'] = packages
    existing['engine'] = engine
    return existing


async def publish_engine_package(
    db: AsyncSession,
    *,
    pk: int,
    os_arch: str,
    version: str,
    data: bytes,
    filename: str,
    expected_sha256: str | None = None,
) -> dict:
    """上传引擎分发包到**公共桶**并写入对应 catalog 行的 ``config_json.engine``（FILMPUB）。

    顺序约束（先上传、可达后再写配置，否则全网 daemon 会去下 404）：
    1. 服务端**权威**计算 sha256 + size（``expected_sha256`` 给了则交叉校验，防上传损坏）。
    2. ``StorageService.upload(category='film_engine')`` 落公共桶，得不签名 CDN 直读 URL。
    3. ``merge_engine_package`` 并入 ``config_json.engine``（新 dict 整体赋回，触发 JSONB 脏标记）。
    4. ``sync_bump('platform_config')`` push 失效 → 在线 daemon 秒级重拉 engine manifest。

    返回写入后的 engine 配置（``{version, packages}``）。
    """
    import hashlib

    from backend.app.hasn.crud.crud_hasn_app_catalog import hasn_app_catalog_dao
    from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump
    from backend.plugin.s3.service.storage_service import StorageService

    actual_sha256 = hashlib.sha256(data).hexdigest()
    if expected_sha256 and expected_sha256.lower() != actual_sha256.lower():
        raise errors.RequestError(
            msg=f'引擎包 sha256 不匹配：客户端 {expected_sha256}，服务端 {actual_sha256}（上传损坏）'
        )

    catalog = await hasn_app_catalog_dao.get(db, pk)
    if not catalog:
        raise errors.NotFoundError(msg=f'应用目录 {pk} 不存在')

    object_key = f'film-engine/{catalog.app_id}/{version}/{filename}'
    ref = await StorageService.upload(
        db,
        data,
        category='film_engine',
        filename=filename,
        content_type='application/zip',
        key=object_key,
    )

    catalog.config_json = merge_engine_package(
        catalog.config_json,
        os_arch=os_arch,
        version=version,
        key=object_key,
        url=ref.stable_url,
        sha256=actual_sha256,
        size=len(data),
    )
    await db.flush()
    await sync_bump('platform_config', db)
    return catalog.config_json['engine']


async def resolve_default_agent_for_app(db: AsyncSession, *, owner_id: str, app_id: str) -> str | None:
    """AppCollab（doc21 §7.2）：打开应用时默认承接的分身 hasn_id。

    算法对称 ``builtin_seeding_service.seed_builtin_tasks`` 的「按类型取一、回退主脑」：
    1. 读 ``catalog.default_agent_type``；
    2. 非空则取 owner 名下 ``builtin_agent_key == default_agent_type`` 的最早活跃分身；
    3. 命中即返回；否则回退主脑（``workbench_pref.primary_agent_id`` → ``role='primary'`` → 首个活跃）。

    无任何活跃分身返回 ``None``（诚实空态，调用方提示先建分身）。同型键 = 一个分身默认服务多应用。
    """
    agents = list(
        (
            await db.execute(
                sa
                .select(HasnAgents)
                .where(HasnAgents.owner_id == owner_id, HasnAgents.status == 'active')
                .order_by(HasnAgents.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not agents:
        return None

    catalog = await get_catalog(db, app_id=app_id)
    want = (catalog.default_agent_type or '') if catalog else ''
    if want:
        for a in agents:
            if a.builtin_agent_key == want:
                return a.hasn_id

    # 回退主脑：pref.primary_agent_id → role=primary → 首个活跃。
    pref_pid = (
        await db.execute(
            sa.select(HasnOwnerWorkbenchPref.primary_agent_id).where(HasnOwnerWorkbenchPref.owner_hasn_id == owner_id)
        )
    ).scalar_one_or_none()
    by_id = {a.hasn_id: a for a in agents}
    if pref_pid and pref_pid in by_id:
        return pref_pid
    for a in agents:
        if a.role == 'primary':
            return a.hasn_id
    return agents[0].hasn_id


async def sweep_expired_entitlements(db: AsyncSession) -> int:
    """把已过期的 active 权益置为 expired（定时兜底，与订阅过期兜底同构）。

    返回受影响行数。``expires_at IS NULL`` 视为永久买断，不受影响。
    """
    now = timezone.now()
    result = await db.execute(
        sa
        .update(HasnAppEntitlement)
        .where(
            HasnAppEntitlement.status == 'active',
            HasnAppEntitlement.expires_at.is_not(None),
            HasnAppEntitlement.expires_at < now,
        )
        .values(status='expired', updated_time=now)
    )
    return result.rowcount or 0


# ============================ C2：catalog 作为展示权威 ============================


def catalog_to_manifest(cat: HasnAppCatalog, *, registry_app: App | None = None) -> dict:
    """把 catalog 行映射为工作台 manifest（与 ``App.to_manifest`` 同形 + ``icon_asset_uri``）。

    launch 字段（ui_kind/window_url/window_origin）catalog 不存——迁移期从本地 ``registry_app``
    overlay；registry 在 C6 退役后由 daemon 本地提供（对齐设计 §3 边界「本地 builtin 只保留 launch 字段」）。
    """
    return {
        'id': cat.app_id,
        'name': cat.name,
        'icon': cat.icon,
        'icon_asset_uri': cat.icon_asset_uri,
        'description': cat.description,
        'scope': list(cat.scope or []),
        'collaboration_mode': cat.collaboration_mode,
        'entry_route': cat.entry_route,
        'install_policy': 'auto' if cat.default_mount else 'manual',
        'requires_role': cat.requires_role,
        'execution_mode': cat.execution_mode,
        'ui_kind': registry_app.ui_kind if registry_app else None,
        'window_url': registry_app.window_url if registry_app else None,
        'window_origin': registry_app.window_origin if registry_app else None,
    }


async def list_published_catalog(db: AsyncSession, *, kind: str | None = None) -> list[HasnAppCatalog]:
    """已上架 catalog 行（按 sort_order 升序），可选按可挂载空间类型（personal/enterprise）过滤。"""
    stmt = (
        sa
        .select(HasnAppCatalog)
        .where(HasnAppCatalog.status == 'published')
        .order_by(HasnAppCatalog.sort_order, HasnAppCatalog.id)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if kind is not None:
        rows = [r for r in rows if kind in (r.scope or [])]
    return rows


async def get_published_catalog(db: AsyncSession, *, app_id: str) -> HasnAppCatalog | None:
    """取单个**已上架** catalog 行；不存在或已下架返回 None（下架即任何人不可用）。"""
    stmt = sa.select(HasnAppCatalog).where(
        HasnAppCatalog.app_id == app_id,
        HasnAppCatalog.status == 'published',
    )
    return (await db.execute(stmt)).scalars().first()


async def get_catalog(db: AsyncSession, *, app_id: str) -> HasnAppCatalog | None:
    """取单个 catalog 行（**任意状态**）；用于准入闸门——下架的付费 app 须 deny 而非静默放行。"""
    stmt = sa.select(HasnAppCatalog).where(HasnAppCatalog.app_id == app_id)
    return (await db.execute(stmt)).scalars().first()


async def resolve_owner_hasn_id(db: AsyncSession, *, user_id: int) -> str | None:
    """唤星平台 user_id → owner hasn_id（h_xxx）；无映射返回 None。"""
    stmt = sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id)
    return (await db.execute(stmt)).scalars().first()


# ============================ C4：商业化准入（resolve_app_access + 三闸门） ============================


def _tier_rank(tier: str | None) -> int:
    """订阅档高低序；未知/None 保守按最低 free=0。"""
    return _TIER_RANK.get((tier or 'free'), 0)


async def _resolve_owner_user_id(db: AsyncSession, owner_hasn_id: str) -> int | None:
    """owner hasn_id（h_xxx）→ 唤星平台 user_id；无映射返回 None（按 free 兜底）。"""
    stmt = sa.select(HasnHumans.user_id).where(HasnHumans.hasn_id == owner_hasn_id)
    user_id = (await db.execute(stmt)).scalars().first()
    return int(user_id) if user_id else None


async def owner_effective_tier(db: AsyncSession, *, owner_hasn_id: str) -> str:
    """owner 的**有效订阅档**（实时读，零新增存储；复用 billing UserSubscription）。

    存储的 ``tier`` 字段过期不降级（只 ``status`` 翻 expired，见 credit_service.get_user_credits_info）；
    准入须按日期重算：``status`` 已过期或订阅结束日已过 → 有效档位回落 ``free``。免费档无结束日永不过期。
    """
    user_id = await _resolve_owner_user_id(db, owner_hasn_id)
    if user_id is None:
        return 'free'
    stmt = sa.select(UserSubscription).where(
        UserSubscription.user_id == user_id,
        UserSubscription.app_code == _DEFAULT_APP_CODE,
    )
    sub = (await db.execute(stmt)).scalars().first()
    if sub is None or sub.tier == 'free' or not sub.tier:
        return 'free'
    now = timezone.now()
    if sub.status == 'expired':
        return 'free'
    if sub.subscription_end_date is not None and now > sub.subscription_end_date:
        return 'free'
    return sub.tier


async def get_active_entitlement(
    db: AsyncSession, *, app_id: str, subject_type: str, subject_id: str
) -> HasnAppEntitlement | None:
    """取该主体对该 app 的**有效**权益行（active 且未过期；``expires_at IS NULL`` 视为永久买断）。"""
    now = timezone.now()
    stmt = sa.select(HasnAppEntitlement).where(
        HasnAppEntitlement.app_id == app_id,
        HasnAppEntitlement.subject_type == subject_type,
        HasnAppEntitlement.subject_id == subject_id,
        HasnAppEntitlement.status == 'active',
        sa.or_(
            HasnAppEntitlement.expires_at.is_(None),
            HasnAppEntitlement.expires_at > now,
        ),
    )
    return (await db.execute(stmt)).scalars().first()


async def _has_used_trial(db: AsyncSession, *, app_id: str, subject_type: str, subject_id: str) -> bool:
    """该主体是否已对此 app 用过试用（任何状态的 source=trial 行都算，强制「只能开一次」）。"""
    stmt = sa.select(HasnAppEntitlement.id).where(
        HasnAppEntitlement.app_id == app_id,
        HasnAppEntitlement.subject_type == subject_type,
        HasnAppEntitlement.subject_id == subject_id,
        HasnAppEntitlement.source == 'trial',
    )
    return (await db.execute(stmt)).scalars().first() is not None


def _price_payload(cat: HasnAppCatalog) -> dict | None:
    """购买价信息（设计 §5.2 price 字段）；无价返回 None。"""
    if cat.price_amount is None:
        return None
    return {
        'amount': float(cat.price_amount),
        'unit': cat.price_unit or 'cny',
        'cycle': cat.billing_cycle or 'once',
    }


async def resolve_app_access(
    db: AsyncSession,
    *,
    catalog: HasnAppCatalog,
    owner_hasn_id: str,
    subject_type: str = 'owner',
) -> dict:
    """统一准入决策函数（设计 §5.2）。返回 AppAccess dict。

    判定顺序（§5.2）：
      1. status != published → disabled（下架，任何人不可用）
      2. free → allowed/free
      3. tier → owner 有效档位 ≥ min_tier ? allowed/tier_ok : need_upgrade（附 trial_available）
      4. purchase → 有 active 权益 ? allowed/entitled（trial 来源则 trialing）: need_purchase（附 trial_available）

    subject_id 取 owner_hasn_id（owner 维度）；企业维度由调用方传 subject_type='enterprise' + 对应 subject_id。
    """
    subject_id = owner_hasn_id

    def _access(
        *,
        allowed: bool,
        reason: str,
        requires: str | None = None,
        trial_available: bool = False,
        entitlement_expires_at: datetime | None = None,
    ) -> dict:
        return {
            'allowed': allowed,
            'reason': reason,
            'requires': requires,
            'min_tier': catalog.min_tier,
            'price': _price_payload(catalog) if requires == 'purchase' else None,
            'trial_available': trial_available,
            'entitlement_expires_at': (entitlement_expires_at.isoformat() if entitlement_expires_at else None),
        }

    if catalog.status != 'published':
        return _access(allowed=False, reason='disabled')

    access_type = catalog.access_type or 'free'
    if access_type == 'free':
        return _access(allowed=True, reason='free')

    if access_type == 'tier':
        effective_tier = await owner_effective_tier(db, owner_hasn_id=owner_hasn_id)
        if _tier_rank(effective_tier) >= _tier_rank(catalog.min_tier):
            return _access(allowed=True, reason='tier_ok')
        trial_available = bool(catalog.trial_days) and not await _has_used_trial(
            db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id
        )
        return _access(allowed=False, reason='need_upgrade', requires='upgrade', trial_available=trial_available)

    if access_type == 'purchase':
        ent = await get_active_entitlement(db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id)
        if ent is not None:
            reason = 'trialing' if ent.source == 'trial' else 'entitled'
            return _access(allowed=True, reason=reason, entitlement_expires_at=ent.expires_at)
        trial_available = bool(catalog.trial_days) and not await _has_used_trial(
            db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id
        )
        return _access(allowed=False, reason='need_purchase', requires='purchase', trial_available=trial_available)

    # 未知 access_type 保守拒绝（不静默放行付费能力）。
    return _access(allowed=False, reason='disabled')


# ============================ C5：权益写操作（试用 / 购买 / admin 授予 / 撤销） ============================

# 购买周期 → 权益时长（天）。``once`` = 永久买断（expires_at=None）。
_PURCHASE_CYCLE_DAYS: dict[str, int] = {'month': 30, 'monthly': 30, 'year': 365, 'yearly': 365}


def purchase_expiry(billing_cycle: str | None):
    """按 billing_cycle 计算购买权益到期时间；``once``/未知周期 → None（永久买断）。"""
    days = _PURCHASE_CYCLE_DAYS.get(billing_cycle or 'once')
    return timezone.now() + timedelta(days=days) if days else None


async def open_trial(
    db: AsyncSession, *, catalog: HasnAppCatalog, owner_hasn_id: str, subject_type: str = 'owner'
) -> HasnAppEntitlement:
    """owner 主动开通一次试用（设计 §5.1）。写 source=trial 权益，``expires_at = now + trial_days``。

    校验：app 须 published + 付费(tier/purchase) + trial_days>0 + 未用过试用 + 无 active 权益。
    违反则抛 ForbiddenError/RequestError（调用方转 4xx）。
    """
    if catalog.status != 'published':
        raise errors.ForbiddenError(msg='应用已下架')
    if (catalog.access_type or 'free') == 'free' or not catalog.trial_days:
        raise errors.RequestError(msg='该应用不支持试用')
    subject_id = owner_hasn_id
    if await _has_used_trial(db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id):
        raise errors.RequestError(msg='试用机会已用过')
    if await get_active_entitlement(db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id):
        raise errors.RequestError(msg='已有有效权益，无需试用')
    now = timezone.now()
    ent = HasnAppEntitlement(
        app_id=catalog.app_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source='trial',
        status='active',
        granted_at=now,
        expires_at=now + timedelta(days=catalog.trial_days),
    )
    db.add(ent)
    await db.flush()
    return ent


async def grant_entitlement(
    db: AsyncSession,
    *,
    app_id: str,
    subject_type: str,
    subject_id: str,
    source: str,
    order_ref: str | None = None,
    expires_at=None,
) -> HasnAppEntitlement:
    """写一条 active 权益（购买回调 / admin 授予共用）。已有 active 则幂等返回（不重复发）。

    唯一约束 ``uq_app_entitlement_active`` 保证每主体每 app 至多一条 active，故先查后写。
    """
    existing = await get_active_entitlement(db, app_id=app_id, subject_type=subject_type, subject_id=subject_id)
    if existing is not None:
        await _post_grant_seed(db, app_id=app_id, subject_type=subject_type, subject_id=subject_id)
        return existing
    ent = HasnAppEntitlement(
        app_id=app_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source=source,
        status='active',
        order_ref=order_ref,
        granted_at=timezone.now(),
        expires_at=expires_at,
    )
    db.add(ent)
    await db.flush()
    await _post_grant_seed(db, app_id=app_id, subject_type=subject_type, subject_id=subject_id)
    return ent


async def _post_grant_seed(db: AsyncSession, *, app_id: str, subject_type: str, subject_id: str) -> None:
    """权益生效后的应用自播种钩子（GE3）。

    企业开通获客（growth）/ 创作（creator）→ 自播种该企业的 playbook（幂等）。平台层不硬依赖应用层：
    用局部 late import，仅在对应 app+enterprise 分支触发；其它应用/个人开通不受影响。播种本身幂等，
    新/旧权益路径都安全调用。
    """
    if subject_type != 'enterprise':
        return
    try:
        enterprise_id = int(subject_id)
    except (TypeError, ValueError):
        return
    if app_id == 'growth':
        from backend.app.hasn_growth.service.enterprise_seed_service import ensure_growth_enterprise_seeded

        await ensure_growth_enterprise_seeded(db, enterprise_id=enterprise_id)
    elif app_id == 'creator':
        from backend.app.hasn_creator.service.enterprise_seed_service import ensure_creator_enterprise_seeded

        await ensure_creator_enterprise_seeded(db, enterprise_id=enterprise_id)


async def revoke_entitlement(db: AsyncSession, *, entitlement_id: int) -> bool:
    """撤销权益（置 status=revoked）。返回是否实际改动。"""
    result = await db.execute(
        sa
        .update(HasnAppEntitlement)
        .where(HasnAppEntitlement.id == entitlement_id, HasnAppEntitlement.status == 'active')
        .values(status='revoked', updated_time=timezone.now())
    )
    return (result.rowcount or 0) > 0


async def list_entitlements(
    db: AsyncSession, *, subject_type: str, subject_id: str, active_only: bool = False
) -> list[HasnAppEntitlement]:
    """列某主体的权益（owner「我的已购」/ admin 查某主体）。"""
    stmt = sa.select(HasnAppEntitlement).where(
        HasnAppEntitlement.subject_type == subject_type,
        HasnAppEntitlement.subject_id == subject_id,
    )
    if active_only:
        now = timezone.now()
        stmt = stmt.where(
            HasnAppEntitlement.status == 'active',
            sa.or_(HasnAppEntitlement.expires_at.is_(None), HasnAppEntitlement.expires_at > now),
        )
    stmt = stmt.order_by(HasnAppEntitlement.id.desc())
    return list((await db.execute(stmt)).scalars().all())
