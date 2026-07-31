"""应用目录 / 权益领域服务（C1 数据层）。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md

职责：
- ``ensure_catalog_seeded``：从 ``app_catalog_registry`` 幂等播种 ``hasn_app_catalog``（迁移 M2）。
  **不回写已存在行的 display/价格**——这是「代码不覆盖运营改动」的关键（区别于 manifest 的
  hash 自愈逻辑，见设计 §6.1）；仅对已废弃且会绕过公共基础设施的配置执行窄范围安全迁移。
- ``sweep_expired_entitlements``：把 ``expires_at < now`` 的 active 权益置 expired（设计 §5.4 定时兜底）。

生成的 ``hasn_app_catalog_service`` / ``hasn_app_entitlement_service`` 负责 Admin CRUD；
本模块只承载播种与兜底这类领域逻辑，避免改动 codegen 产物。
"""

from __future__ import annotations

import copy
import json
import re

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import sqlalchemy as sa

from backend.app.billing.model import UserSubscription
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_app_beta_access import HasnAppBetaAccess
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn.model.hasn_app_seat import HasnAppSeat
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.app_catalog_registry import App, app_catalog_registry
from backend.app.hasn_design.manifest import DESIGN_BUSINESS_PROMPT
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
from backend.common.exception import errors
from backend.database.result import affected_rows
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from fastapi import UploadFile
    from sqlalchemy.ext.asyncio import AsyncSession

# 默认应用标识（订阅档与 billing 同源，见 [[project_billing_newapi_authoritative_source]]）。
_DEFAULT_APP_CODE = 'huanxing'

# 订阅档高低序（设计 §5.4 枚举 free/pro/advanced/flagship）。未知档位保守按最低 free 处理。
_TIER_RANK: dict[str, int] = {'free': 0, 'pro': 1, 'advanced': 2, 'flagship': 3}

# 工作台排序（小在前）。未列出的 app 落到默认值之后。
_CATALOG_SORT_ORDER: dict[str, int] = {
    'project': 5,  # 项目管理（第三条轴「为了哪件事」聚合门面，doc38；一级容器置顶，install_policy=auto 默认挂载）
    'knowledge': 10,
    'community': 20,
    'deck': 35,
    'publish': 40,
    'growth': 45,  # 获客（设计 §3.2 约 40，置于 publish 之后；default_mount=FALSE 由 install_policy=manual 推导）
    'creator': 50,  # 创作运营（置于 growth 之后；default_mount=FALSE 由 install_policy=manual 推导）
    'film': 55,  # 视频生成（源自 VideoClaw；default_mount=FALSE 由 install_policy=manual 推导）
    'reel': 57,  # 短视频合成（源自 MoneyPrinterTurbo，瘦引擎应用；default_mount=FALSE 由 install_policy=manual 推导）
    'imagelab': 58,  # 图像处理（图坊，自研本地引擎；default_mount=FALSE 由 install_policy=manual 推导）
    'copilot': 60,  # 会议副驾（local_tool 无 Agent 工具；default_mount=FALSE 由 install_policy=manual 推导）
    'plan': 65,  # 规划与目标管理（PIM；default_mount=FALSE 由 install_policy=manual 推导）
    'finance': 70,  # 金融投研（local_tool 本地应用；default_mount=FALSE 由 install_policy=manual 推导）
    'quant': 75,  # 量化交易（cloud-brokered 量化工作台，模块 14 doc23；default_mount=FALSE 由 manual 推导）
    'studio': 76,  # 统一视频引擎（cloud-brokered 视频工作台，模块 14 doc22；default_mount=FALSE 由 manual 推导）
    'design': 78,  # 矢量设计（local_tool 本地 sidecar，源自 OpenPencil，doc27；default_mount=FALSE 由 manual 推导）
    'computer_use': 80,  # 桌面控制（local_tool 能力型应用，模块 23 V2；default_mount=FALSE 由 manual 推导）
}
_DEFAULT_SORT_ORDER = 100

MAX_FINANCE_RELEASE_MANIFEST_BYTES = 1024 * 1024
_FINANCE_RELEASE_FIELDS = {
    'schema_version',
    'artifact_id',
    'version',
    'release_sequence',
    'channel',
    'issued_at',
    'expires_at',
    'minimum_daemon_version',
    'packages',
    'revocations',
    'key_id',
    'signature',
}
_FINANCE_PACKAGE_FIELDS = {
    'url',
    'sha256',
    'compressed_size',
    'installed_size_limit',
    'file_manifest_sha256',
}
_FINANCE_REVOCATION_FIELDS = {
    'version',
    'platform',
    'sha256',
    'revoked_at',
    'reason',
    'critical',
}
_FINANCE_RELEASE_PLATFORMS = {
    'darwin-aarch64',
    'darwin-x86_64',
    'linux-aarch64',
    'linux-x86_64',
    'win-aarch64',
    'win-x86_64',
}

# AppCollab（doc21 §4.3/§5.4）：应用默认承接的内置 agent 类型键 + 唤起分身注入的业务提示词模板。
# 类型键 = hub 内置模板的 ``builtin_key``（``builtin: true``）；daemon ``resolve_default_agent_for_app`` 按
# ``hasn_agents.builtin_agent_key == default_agent_type`` 取 owner 名下分身、命中即返回否则回退主脑。
# 2026-07-12 内置分身收敛为 3 个（全能助理 assistant / 创作专家 content_operator / 分析专家 analyst），
# 全部应用的默认承接分身一律归口这 3 个；planner/meeting_copilot/designer/developer/sales_advisor 已退为
# 可选市场模板（不再 builtin），其对应应用改由 3 个内置承接：
#   - ``assistant``（全能助理·主脑）：knowledge/hasn_task/plan/copilot/growth；
#   - ``content_operator``（创作专家）：community/deck/creator/film/reel/imagelab/studio/designsystem/design/publish；
#   - ``analyst``（分析专家）：finance/quant。
# work_session_system_prompt（业务提示词）按应用区分、保留不动——只换承接分身，不改每应用的执行提示词。
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
    # 项目管理归「全能助理（assistant）」——项目是「为了哪件事」的业务容器门面（doc38），
    # 分身建项目/在项目内推进，不接管各应用容器（三条铁律）。
    'project': (
        'assistant',
        '你是项目管理应用的执行分身：帮主人把「为了哪件事」的活儿收进项目——建项目（一句话目标'
        '→ hasn.project.create）、把已有资源挂靠进来（hasn.project.link 知识库/获客项目/图坊项目/'
        '站点/deck…）、按里程碑推进（hasn.project.milestone.*）、在项目内派发分身干活。项目只回答'
        '「为了哪件事」——不替代各应用（知识库/获客/图坊仍在各自应用里操作），只做聚合与推进。'
        '只调用 hasn.project.* 工具，挂靠/摘出统一经工具（不擅自跨应用改数据），零 fake，失败如实报错。',
    ),
    # 社区归「内容运营官（content_operator）」——与 deck/creator/film/designsystem 同一分身。
    'community': (
        'content_operator',
        '你是社区应用的执行分身：替主人在社区发现内容、发帖与互动、经营关注关系；'
        '只调用社区相关工具，对客可见内容须得体专业，零 fake，失败如实报错。',
    ),
    # 网页发布归口「创作专家（content_operator）」分身（2026-07-12 内置收敛为 3：developer 退为可选模板，
    # publish 改由 content_operator 承接）。业务提示词不变——分身仍按建站全流程（设计→开发→本地预览→
    # 打包成单文件→hasn.publish.* 发布），与 daemon publish/dispatch.rs::PUBLISH_BUSINESS_PROMPT 同义。
    'publish': (
        'content_operator',
        '你是网页发布应用的编程开发专家分身：把主人的想法一条龙做成可访问的线上网页。先澄清网站类型/受众/'
        '核心目标/风格（关键信息缺失且有歧义才用 hasn.session.ask 问，能自主定的别问）；再做视觉设计 → '
        '前端开发（简单页手写单文件 index.html / 复杂页用 React+Tailwind+shadcn 脚手架）→ 本地预览自检'
        '（Playwright，无控制台报错）→ 打包成单文件自包含 HTML → 调 hasn.publish.create（path 指向 '
        'bundle.html/index.html）发布成稳定分享链接（/s/{slug}）并按需管理可见性。能做静态站与客户端动态 SPA'
        '（浏览器本地存储 / 调外部公开 API）；自有服务端+数据库超出当前发布托管能力，须如实告知主人并给替代'
        '方案（改客户端动态 / 只交付代码由主人自部署），绝不假装把带数据库的动态站发布成功。真写代码、真打包、'
        '真调工具拿真 URL，零 fake，失败如实报错。',
    ),
    # 获客归口「全能助理（assistant）」分身当商务助理（2026-07-12 内置收敛为 3：sales_advisor 退为可选模板，
    # growth 改由 assistant 承接）。业务提示词不变——请求线索 = 找→分析→决策闭环（doc10）：用
    # search_companies/lookup_company 读穿工具找线索（自动进主人线索池，无需分辨来源），分析后按主人需求
    # 决定加为客户（lead.qualify）/ 找商机（opportunity.create）/ 向主人提问（hasn.session.ask）。
    'growth': (
        'assistant',
        '你是获客应用的执行分身（销售顾问）：替主人找线索、做分析、按主人需求决策下一步，沉淀可复用的获客打法。'
        '请求线索时：先用 hasn.growth.search_companies / lookup_company 找线索（结果自动进主人线索池，你无需分辨'
        '线索是查池命中还是新查来的），再结合主人画像分析哪些值得跟；然后按主人的需求决定——加为客户'
        '（lead.qualify）、继续找商机（opportunity.create），还是拿不准就用 hasn.session.ask 问主人。'
        '只调用 hasn.growth.* 工具，合规先行、对外触达过主人确认，每一步对主人透明，零 fake，失败如实报错。',
    ),
    'deck': (
        'content_operator',
        '你是演示文稿应用的执行分身：把主人的诉求做成结构清晰、视觉专业的演示文稿，'
        '只调用 hasn.deck.* 工具就地生成与精修；产出对客可用的成品，零 fake，失败如实报错。',
    ),
    # 设计系统归口「创作专家（content_operator）」分身（2026-07-12 内置收敛为 3：designer 退为可选模板，
    # designsystem/design 均改由 content_operator 承接）。工作会话提示词按应用区分——本条保留设计系统专属提示词。
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
    # 不另起「视频分身」（AC-P6 福仔拍板复用 content_operator）。
    # 一个分身默认服务 deck/designsystem/creator/film 四应用。
    'film': (
        'content_operator',
        '你是视频生成应用的执行分身：把主人的创意做成完整的短视频，按脚本→角色设定→分镜→参考图→'
        '片段生成→合成的流水线推进；只调用 hasn.film.* 工具就地生成与精修；产出对客可用的成品，'
        '零 fake，失败如实报错。',
    ),
    # 短视频创作（源自 MoneyPrinterTurbo）归「内容运营官（content_operator）」。doc29 起 reel 有自己的
    # 轻量项目管理 + 三种发起（一键 / 分身采访代发起 / 分身工具编排）；此业务提示词与 daemon
    # reel/dispatch.rs::REEL_BUSINESS_PROMPT 同义（按需采访 + 起草定稿 + 出片）。一个分身默认服务
    # deck/designsystem/creator/film/reel 等多应用。
    'reel': (
        'content_operator',
        '你是短视频创作专家分身：把主人的需求做成可直接发布的短视频。先按你对主人的了解（做什么 / '
        '主要诉求 / 产品信息 / 过往偏好）判断信息够不够——够就直接做、不必采访（能自主决策的别每次都问），'
        '只有关键信息缺失或有歧义（调性 / 受众 / 平台画幅 / 时长 / 卖点 / CTA / 素材）才用 '
        'hasn.session.ask 问清、绝不硬猜；再用 hasn.reel.script.draft 起草文案（定稿摊给主人确认）→ '
        '一把梭 hasn.reel.generate 或分步合成出片 → hasn.reel.artifact.upload 登记成片。'
        '真实引擎本地合成、本地优先不自动上云，零 fake，失败如实报错。',
    ),
    # 图像处理（图坊，自研本地引擎，图坊架构 §5.5/§5.7）也归「内容运营官（content_operator）」——无专有「修图师」
    # 分身，任意分身皆可操作，hasn.imagelab.* 工具面与技能所有分身共享（福仔 2026-07-02 纠正）；默认承接
    # content_operator。一个分身默认服务 deck/designsystem/creator/film/reel/imagelab 等多应用。
    'imagelab': (
        'content_operator',
        '你是图坊（图像处理应用）的执行分身：把主人的图片处理需求做成对客可用的成品。'
        '先用 hasn.imagelab.analyze 读现状（尺寸/格式/透明度/主体）再动手，别盲目开工；'
        '复杂或批量需求用 hasn.imagelab.pipeline / batch 组「处理配方」一次编排（去背景→裁剪→水印→压缩→转格式…），'
        '不要一步步单发；非破坏性处理（裁剪/缩放/调色/滤镜/格式/压缩/去背景/拼图/动画）可自由做（默认不覆盖原图、'
        '产物只落本地、可回滚），破坏性操作（inpaint 去物体/去水印，hasn.imagelab.retouch）和生成式操作'
        '（hasn.imagelab.generate 花积分）先与主人确认；批量前先明确输入范围与预期产出数、大批量提交后经 '
        'hasn.imagelab.job.get 轮询进度；完成用 hasn.imagelab.export 把产物写到本地输出目录并登记，回禀主人，'
        '需要分享才用 hasn.imagelab.share 上云发好友/群。文案/配色/水印文字等创意与审美判断摊给主人定、不擅自拍板。'
        '真实引擎本地处理、产物本地优先不自动上云，零 fake，失败如实报错。',
    ),
    # 会议副驾归口「全能助理（assistant）」分身（2026-07-12 内置收敛为 3：meeting_copilot 退为可选模板，
    # copilot 改由 assistant 承接）。业务提示词不变——分身仍按会议实时副驾方式工作。
    'copilot': (
        'assistant',
        '你是会议副驾的执行分身：边听会议/通话的双方对话，边给关键要点、可追问的问题、待办与易错点；'
        '克制不刷屏、宁缺毋滥。会后按结构化纪要方法产出纪要落产物。只在本工作会话内工作，'
        '听不清就如实标注，零 fake、失败如实报错。',
    ),
    # 规划与目标管理归口「全能助理（assistant）」分身（2026-07-12 内置收敛为 3：planner 退为可选模板，
    # plan 改由 assistant 承接）。业务提示词不变——分身既当参谋长（拆目标/排计划/简报复盘）又当执行秘书（捕获/排期/委托）。
    'plan': (
        'assistant',
        '你是主人的私人参谋长 + 执行秘书：帮主人把模糊想法收敛成目标/关键结果，拆成可执行的计划与待办，'
        '合理排期到日历，每日给简报、定期做复盘；只调用 hasn.plan.* 工具就地管理主人的规划数据，'
        '尊重主人的最终决定权，零 fake、失败如实报错。',
    ),
    # 金融投研使用专属「投研分析师（analyst）」分身；工具由 hasn-node 本地引擎执行。
    'finance': (
        'analyst',
        '你是主人的投研分析师：使用 hasn.finance.* 本地工具完成数据研究、专家团队、策略回测、'
        '交易复盘、自选盯盘与简报。异步任务启动后立即结束当前轮，不自行轮询，由后台回灌结果；'
        '不执行真实下单。所有数据仅供参考、不构成投资建议，引用须标注口径与日期；'
        '取不到就如实说明，零 fake、失败如实报错。',
    ),
    # 量化归口「金融理财专家（analyst）」分身（2026-07-03 一类应用一模板：quant_trader 折叠进 analyst）。
    # 模板同 finance 共用 analyst（builtin_key=analyst），但工作会话提示词按应用区分——本条保留量化回测专属提示词。
    # 本期 P0–P5 只做回测研究（零资金风险）：写策略 → 跑回测 → 读绩效 → 迭代优化；实盘线 P6+ 受硬闸不开。
    'quant': (
        'analyst',
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
    # 矢量设计（source OpenPencil，模块 14 doc27）归口「创作专家（content_operator）」分身（2026-07-12
    # 内置收敛为 3：designer 退为可选模板，design/designsystem 均改由 content_operator 承接）。
    # 业务提示词单一事实源在 manifest.DESIGN_BUSINESS_PROMPT（教 open_document→分层出图→export 登记产物→定稿摊主人）。
    'design': (
        'content_operator',
        DESIGN_BUSINESS_PROMPT,
    ),
    # 桌面控制归口「全能助理（assistant）」——通用执行型分身代主人操作电脑（看屏→定位→点击/输入）。
    'computer_use': (
        'assistant',
        '你是桌面控制应用的执行分身：替主人在真实桌面上完成 GUI 操作。先用 hasn.computer.list_apps / '
        'capture 看清目标 App 与界面（据 SOM 元素编号精准定位），再一步步 click / type / set_value / drag / '
        'key / scroll 操作；每一步动作前先观察、动作后再截图核实效果（据 source_capture_id 校验界面未漂移），'
        '不据陈旧截图盲操作。全屏截图（capture_screen）与控制动作默认需主人确认；高危 App（终端/系统设置/'
        '支付密码类）一律逐次审批，绝不自动放行。真看屏、真操作、真核实，零 fake，失败或被拒如实报错。',
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
    # 短视频合成（reel，源自 MoneyPrinterTurbo，doc19 §6.4）：应用只选择 llm（文案/搜索词）和
    # 统一语音 voice_id；TTS 模型/本地或网关路由由全局 SpeechService/PDC 权威决定。
    # 出厂给出**完整骨架 + 示例值**（管理端「编辑配置」开箱即见结构），运营/主人改值即可：
    #   - models.llm 是 **failover 列表**（首个为主、其余兜底）：默认 agnes-2.0-flash 为主
    #     + deepseek-v4-pro / qwen3.7-plus 兜底（均为 new-api 已开通的真实文案模型，reel **无需开通
    #     视频渠道**）。agnes 单渠道偶发 503/超时（vllm 自建上游 ~10s/次），有兜底则自动切换不硬失败；
    #     运营可在管理端「编辑配置」换主/增删兜底。
    #   - tts 只保留统一语音 catalog 的 voice_id，禁止应用选择 provider 或模型。
    #   - material.platform_keys 是平台统一兜底素材 key（M2）：**留空占位，运营在管理端填**，绝不硬编码
    #     真实 key；owner 可在应用内自填（多 key 轮换避限流，doc19 §6.3）。
    #   - engine.bundled_deps=['ffmpeg','imagemagick']（reel 特有本地合成依赖，M3/N5）；engine manifest
    #     （version + 按架构 packages）留空：dev 用 fork 源码树即可跑，prod 由运营经管理端/FILMPUB 填。
    'reel': {
        'models': {
            'llm': ['agnes-2.0-flash', 'deepseek-v4-pro', 'qwen3.7-plus'],
        },
        'tts': {
            'voice_id': 'Cherry',
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
    # 图像处理（imagelab，图坊，自研本地引擎，图坊架构 §5.1/§5.5）：确定性像素处理走自研本地引擎（ffmpeg/rembg/
    # Pillow/scipy/libwebp/opencv），生成类不自建模型（桥接平台 hasn.image.generate）→ 故 config_json 只承载
    # engine 分发骨架 + 按需下载的 ML 模型清单，**无 image/video 生成模型**（不烧生成 token）。
    #   - engine.bundled_deps=['ffmpeg','libwebp']（图坊本地处理特有依赖，含动画组装 cwebp/webpmux）；
    #     ml_models 是**按需下载**的重模型（rembg 抠图 birefnet ~1GB / lama inpaint / esrgan 超分 / ocr），
    #     不随引擎包强制下发，daemon engine.rs 首用某算子时按需下载（对齐星仔 U2NET_HOME + reel/film 引擎下载）。
    #   - version + 按架构 packages 留空：dev 用自研引擎源码树即可跑，prod 由运营经管理端/FILMPUB 托管引擎包后填。
    'imagelab': {
        'engine': {
            'version': '',
            'packages': {},
            'bundled_deps': ['ffmpeg', 'libwebp'],
            'ml_models': ['birefnet-general', 'lama', 'realesrgan', 'paddleocr'],
        },
    },
    # 矢量设计（design，源自 OpenPencil，doc27 §4.3/§7/§9）：本地 sidecar = OpenPencil node-server web 编辑器 +
    # pen-mcp 双进程，引擎包随桌面端下发（无云端算力成本，区别于 studio 云服务）。design 生成走分身自己的 LLM
    # （new-api），sidecar/pen-mcp 本身做 DSL 解析/自动布局/渲染（无独立模型配置）——故 config_json 只承载
    # engine 分发骨架（bundled_deps=['node']：sidecar 是 Node 服务，需 Node runtime，doc27 §9 风险#4）。
    # version + 按架构 packages 留空：dev 用 fork 源码树即可跑，prod 由运营经管理端/FILMPUB 填。
    'design': {
        'engine': {
            'version': '',
            'packages': {},
            'bundled_deps': ['node'],
        },
    },
}


# 应用图标 CDN 约定（与 backend/scripts/upload_app_icons.py 同源）：公共桶固定路径
# ``app-icons/{app_id}.svg``——这些 URL 即部署端 webui ``AppCard`` 的彩色品牌图标源
# （``icon_asset_uri`` 优先于单色 token；打包端 token 回落单色，故新应用必须落 CDN 图）。
_APP_ICON_CDN_BASE = 'http://hasn-pub-cdn.dcfuture.cn/huanxing/app-icons'

# seed 阶段即带图标的应用（app_id → CDN URL）。
# 背景（血泪）：``ensure_catalog_seeded`` 是 INSERT-only，新应用首次播种若 icon_asset_uri=None，
# 部署端 webui 就落单色兜底；而「补图迁移」在部署流程里可能**先于**本次 seed 跑（→UPDATE 0 空转、
# 且被 runner 标记已应用）→ 新应用图标永久为空。故对新增/易漏的应用在 seed 阶段直接带上确定性
# CDN 图标 URL，一劳永逸。存量行由 INSERT-only 保留、不受影响；未列出的应用仍 seed 成 None
# （既有应用的图标来自已在各环境应用过的补图迁移，行为不变；deck 用自定义图故不在此列）。
_CATALOG_SEED_ICON_ASSET_URI = {
    'computer_use': f'{_APP_ICON_CDN_BASE}/computer_use.svg',
    'finance': f'{_APP_ICON_CDN_BASE}/finance.svg',
    'project': f'{_APP_ICON_CDN_BASE}/project.svg',
}


def _catalog_row_from_app(app: App) -> dict:
    """把 App 映射为 catalog 行的默认值（迁移期单一来源）。

    新增字段（source/status/商业化…）取保守默认：全部内置、已上架、免费。
    """
    return {
        'app_id': app.id,
        'name': app.name,
        'icon': app.icon,
        'icon_asset_uri': _CATALOG_SEED_ICON_ASSET_URI.get(app.id),
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


def _normalize_reel_speech_config(config_json: dict | None) -> dict:
    """删除 Reel 已废弃的应用级语音路由，只保留运营选择的统一 voice_id。"""
    normalized = dict(config_json or {})
    models = dict(normalized.get('models') or {})
    models.pop('tts', None)
    models.pop('stt', None)
    normalized['models'] = models
    legacy_tts = normalized.get('tts')
    voice_id = legacy_tts.get('voice_id') if isinstance(legacy_tts, dict) else None
    normalized['tts'] = {'voice_id': voice_id.strip() if isinstance(voice_id, str) and voice_id.strip() else 'Cherry'}
    normalized.pop('subtitle', None)
    return normalized


async def ensure_catalog_seeded(db: AsyncSession) -> int:
    """幂等播种 catalog；已存在行保留运营字段，仅清除 Reel 已废弃的语音旁路配置。

    返回新插入的行数。可在部署 reconcile / 测试夹具中调用。
    """
    existing_rows = list((await db.execute(sa.select(HasnAppCatalog))).scalars().all())
    existing = {row.app_id: row for row in existing_rows}
    changed = False
    if reel := existing.get('reel'):
        normalized = _normalize_reel_speech_config(reel.config_json)
        if normalized != reel.config_json:
            reel.config_json = normalized
            changed = True
    inserted = 0
    for app in app_catalog_registry.list():
        if app.id in existing:
            continue
        db.add(HasnAppCatalog(**_catalog_row_from_app(app)))
        inserted += 1
    if inserted or changed:
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


_SIGNED_ENGINE_DOCUMENT_FIELDS = {'payload', 'signature'}
_SIGNED_ENGINE_PAYLOAD_FIELDS = {
    'schema_version',
    'artifact_id',
    'version',
    'release_sequence',
    'channel',
    'issued_at',
    'expires_at',
    'minimum_daemon_version',
    'revoked',
    'key_id',
    'packages',
}
_SIGNED_ENGINE_PACKAGE_FIELDS = {
    'key',
    'url',
    'sha256',
    'compressed_size',
    'installed_size',
    'file_manifest_sha256',
}
_SIGNED_ENGINE_SHA256_RE = re.compile(r'^[0-9a-fA-F]{64}$')
_SIGNED_ENGINE_SIGNATURE_RE = re.compile(r'^[0-9a-fA-F]{128}$')
_SIGNED_ENGINE_PLATFORM_RE = re.compile(r'^(macos|windows|linux)-[A-Za-z0-9._-]{1,128}$')
_SIGNED_ENGINE_TOKEN_RE = re.compile(r'^[A-Za-z0-9._-]{1,128}$')
_SIGNED_ENGINE_VERSION_RE = re.compile(r'^[A-Za-z0-9._-]{1,128}$')
_SIGNED_ENGINE_SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')


def _require_exact_fields(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise errors.RequestError(msg=f'图坊签名 manifest {label}字段不符合 schema v2')
    return value


def _parse_signed_engine_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise errors.RequestError(msg=f'图坊签名 manifest {field} 必须是 RFC3339 时间')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise errors.RequestError(msg=f'图坊签名 manifest {field} 必须是 RFC3339 时间') from exc
    if parsed.tzinfo is None:
        raise errors.RequestError(msg=f'图坊签名 manifest {field} 必须带时区')
    return parsed


def _validate_signed_engine_package(
    *,
    app_id: str,
    version: str,
    platform_key: object,
    package_value: object,
) -> dict:
    if not isinstance(platform_key, str) or not _SIGNED_ENGINE_PLATFORM_RE.fullmatch(platform_key):
        raise errors.RequestError(msg=f'图坊签名 manifest 平台键无效：{platform_key}')
    package = _require_exact_fields(
        package_value, _SIGNED_ENGINE_PACKAGE_FIELDS, f'packages.{platform_key} ',
    )
    object_key = package['key']
    expected_prefix = f'runtime-engine/{app_id}/{version}/'
    if (
        not isinstance(object_key, str)
        or not object_key.startswith(expected_prefix)
        or '..' in object_key
        or any(character.isspace() for character in object_key)
    ):
        raise errors.RequestError(msg=f'图坊签名 manifest 对象 key 必须位于 {expected_prefix}')
    url = package['url']
    if not isinstance(url, str):
        raise errors.RequestError(msg='图坊签名 manifest 包 URL 无效')
    parsed_url = urlparse(url)
    loopback = parsed_url.scheme == 'http' and parsed_url.hostname in {'localhost', '127.0.0.1', '::1'}
    if parsed_url.scheme != 'https' and not loopback:
        raise errors.RequestError(msg='图坊签名 manifest 包 URL 必须是 HTTPS 或 loopback HTTP')
    for field in ('sha256', 'file_manifest_sha256'):
        digest = package[field]
        if not isinstance(digest, str) or not _SIGNED_ENGINE_SHA256_RE.fullmatch(digest):
            raise errors.RequestError(msg=f'图坊签名 manifest {field} 必须是 64 位 sha256')
    for field in ('compressed_size', 'installed_size'):
        size = package[field]
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise errors.RequestError(msg=f'图坊签名 manifest {field} 必须是正整数')
    return package


def _validate_signed_engine_manifest(*, app_id: str, document: object) -> dict:
    signed = _require_exact_fields(document, _SIGNED_ENGINE_DOCUMENT_FIELDS, '顶层')
    signature = signed['signature']
    if not isinstance(signature, str) or not _SIGNED_ENGINE_SIGNATURE_RE.fullmatch(signature):
        raise errors.RequestError(msg='图坊签名 manifest signature 必须是 128 位 Ed25519 hex')
    payload = _require_exact_fields(signed['payload'], _SIGNED_ENGINE_PAYLOAD_FIELDS, 'payload ')
    if payload['schema_version'] != 2:
        raise errors.RequestError(msg='图坊签名 manifest schema_version 必须为 2')
    expected_artifact_id = f'app.engine.{app_id}'
    if payload['artifact_id'] != expected_artifact_id:
        raise errors.RequestError(msg=f'图坊签名 manifest artifact_id 必须为 {expected_artifact_id}')
    version = payload['version']
    if (
        not isinstance(version, str)
        or version in {'.', '..'}
        or not _SIGNED_ENGINE_VERSION_RE.fullmatch(version)
    ):
        raise errors.RequestError(msg='图坊签名 manifest version 无效')
    sequence = payload['release_sequence']
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise errors.RequestError(msg='图坊签名 manifest release_sequence 必须是正整数')
    for field in ('channel', 'key_id'):
        value = payload[field]
        if not isinstance(value, str) or not _SIGNED_ENGINE_TOKEN_RE.fullmatch(value):
            raise errors.RequestError(msg=f'图坊签名 manifest {field} 无效')
    minimum_daemon_version = payload['minimum_daemon_version']
    if (
        not isinstance(minimum_daemon_version, str)
        or not _SIGNED_ENGINE_SEMVER_RE.fullmatch(minimum_daemon_version)
    ):
        raise errors.RequestError(msg='图坊签名 manifest minimum_daemon_version 无效')
    if not isinstance(payload['revoked'], bool):
        raise errors.RequestError(msg='图坊签名 manifest revoked 必须是布尔值')
    issued_at = _parse_signed_engine_time(payload['issued_at'], 'issued_at')
    expires_at = _parse_signed_engine_time(payload['expires_at'], 'expires_at')
    if expires_at <= issued_at:
        raise errors.RequestError(msg='图坊签名 manifest expires_at 必须晚于 issued_at')
    packages = payload['packages']
    if not isinstance(packages, dict) or not packages:
        raise errors.RequestError(msg='图坊签名 manifest packages 不能为空')
    for platform_key, package in packages.items():
        _validate_signed_engine_package(
            app_id=app_id,
            version=version,
            platform_key=platform_key,
            package_value=package,
        )
    return signed


def merge_signed_engine_manifest(
    config_json: dict | None,
    *,
    app_id: str,
    document: dict,
) -> dict:
    """严格校验并保存 schema v2 签名引擎清单；云端不持有发布私钥，只做哑存储。

    发布序列在云端也保持单调：低序列拒绝重放，相同序列只允许字节语义完全幂等；同一版本的
    同平台摘要不得改变。daemon 仍会用内置信任根验签，云端结构校验不能替代客户端信任门。
    """
    signed = copy.deepcopy(_validate_signed_engine_manifest(app_id=app_id, document=document))
    existing = copy.deepcopy(config_json or {})
    current = existing.get('engine')
    if (
        isinstance(current, dict)
        and isinstance(current.get('payload'), dict)
        and current['payload'].get('schema_version') == 2
    ):
        previous_payload = current['payload']
        incoming_payload = signed['payload']
        previous_sequence = previous_payload.get('release_sequence')
        incoming_sequence = incoming_payload['release_sequence']
        if isinstance(previous_sequence, int):
            if incoming_sequence < previous_sequence:
                raise errors.RequestError(msg='图坊签名 manifest 发布序列重放')
            if incoming_sequence == previous_sequence:
                if current != signed:
                    raise errors.RequestError(msg='图坊签名 manifest 相同发布序列内容不一致')
                return existing
        if previous_payload.get('version') == incoming_payload['version']:
            previous_packages = previous_payload.get('packages') or {}
            for platform_key, incoming_package in incoming_payload['packages'].items():
                previous_package = previous_packages.get(platform_key)
                if (
                    isinstance(previous_package, dict)
                    and previous_package.get('sha256') != incoming_package['sha256']
                ):
                    raise errors.RequestError(
                        msg=f'图坊签名 manifest 同版本异摘要：{platform_key}',
                    )
    existing['engine'] = signed
    return existing


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


def _request_error(message: str) -> errors.RequestError:
    return errors.RequestError(msg=f'金融引擎发布清单无效：{message}')


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in '0123456789abcdef' for character in value)
    )


def _parse_finance_release_time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith('Z'):
        raise _request_error(f'{field} 必须是 UTC RFC3339 时间')
    try:
        parsed = datetime.fromisoformat(f'{value[:-1]}+00:00')
    except ValueError as exc:
        raise _request_error(f'{field} 不是合法时间') from exc
    if parsed.tzinfo != UTC:
        raise _request_error(f'{field} 必须使用 UTC')
    return parsed


def _validate_finance_release_package(platform: object, package: object) -> None:
    if platform not in _FINANCE_RELEASE_PLATFORMS:
        raise _request_error(f'不支持平台 {platform!r}')
    if not isinstance(package, dict) or set(package) != _FINANCE_PACKAGE_FIELDS:
        raise _request_error(f'packages.{platform} 字段不完整')
    try:
        parsed_url = urlparse(package['url']) if isinstance(package['url'], str) else None
    except ValueError as exc:
        raise _request_error(f'packages.{platform}.url 不是合法 URL') from exc
    loopback = (
        parsed_url is not None
        and parsed_url.scheme == 'http'
        and parsed_url.hostname in {'127.0.0.1', '::1', 'localhost'}
    )
    if parsed_url is None or not ((parsed_url.scheme == 'https' and parsed_url.netloc) or loopback):
        raise _request_error(f'packages.{platform}.url 只允许 https 或 loopback http')
    if not _is_lower_hex(package['sha256'], 64):
        raise _request_error(f'packages.{platform}.sha256 必须是小写 SHA-256')
    if not _is_lower_hex(package['file_manifest_sha256'], 64):
        raise _request_error(f'packages.{platform}.file_manifest_sha256 必须是小写 SHA-256')
    for field in ('compressed_size', 'installed_size_limit'):
        if not _is_positive_int(package[field]):
            raise _request_error(f'packages.{platform}.{field} 必须是正整数')


def _validate_finance_release_identity(manifest: dict) -> None:
    if manifest['schema_version'] != 2 or manifest['artifact_id'] != 'app.engine.finance':
        raise _request_error('schema_version 或 artifact_id 不匹配')
    if not _is_positive_int(manifest['release_sequence']):
        raise _request_error('release_sequence 必须是正整数')
    for field in ('version', 'channel', 'minimum_daemon_version', 'key_id'):
        if not isinstance(manifest[field], str) or not manifest[field].strip():
            raise _request_error(f'{field} 必须是非空字符串')
    if not _is_lower_hex(manifest['signature'], 128):
        raise _request_error('signature 必须是 128 位小写十六进制')
    issued_at = _parse_finance_release_time(manifest['issued_at'], 'issued_at')
    expires_at = _parse_finance_release_time(manifest['expires_at'], 'expires_at')
    if expires_at <= issued_at:
        raise _request_error('expires_at 必须晚于 issued_at')


def _validate_finance_release_revocations(revocations: object) -> None:
    if not isinstance(revocations, list):
        raise _request_error('revocations 必须是数组')
    for index, revocation in enumerate(revocations):
        if not isinstance(revocation, dict) or set(revocation) != _FINANCE_REVOCATION_FIELDS:
            raise _request_error(f'revocations[{index}] 字段不完整')
        for field in ('version', 'reason'):
            if not isinstance(revocation[field], str) or not revocation[field].strip():
                raise _request_error(f'revocations[{index}].{field} 必须是非空字符串')
        if revocation['platform'] not in _FINANCE_RELEASE_PLATFORMS:
            raise _request_error(f'revocations[{index}].platform 不受支持')
        if not _is_lower_hex(revocation['sha256'], 64):
            raise _request_error(f'revocations[{index}].sha256 必须是小写 SHA-256')
        _parse_finance_release_time(revocation['revoked_at'], f'revocations[{index}].revoked_at')
        if not isinstance(revocation['critical'], bool):
            raise _request_error(f'revocations[{index}].critical 必须是布尔值')


def _validate_finance_release_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict) or set(manifest) != _FINANCE_RELEASE_FIELDS:
        raise _request_error('顶层字段必须与 schema v2 完全一致')
    _validate_finance_release_identity(manifest)
    packages = manifest['packages']
    if not isinstance(packages, dict) or not packages:
        raise _request_error('packages 必须是非空对象')
    for platform, package in packages.items():
        _validate_finance_release_package(platform, package)
    _validate_finance_release_revocations(manifest['revocations'])
    return manifest


def merge_finance_engine_release(config_json: dict | None, manifest: object) -> dict:
    """校验并写入 Finance 签名发布清单，保留已上传包与其它应用配置。"""
    manifest = _validate_finance_release_manifest(manifest)
    existing = copy.deepcopy(config_json or {})
    engine = existing.get('engine')
    if not isinstance(engine, dict) or engine.get('version') != manifest['version']:
        raise _request_error('清单版本尚未完成公共包上传')
    uploaded_packages = engine.get('packages')
    if not isinstance(uploaded_packages, dict):
        raise _request_error('缺少已上传公共包元数据')
    for platform, package in manifest['packages'].items():
        uploaded = uploaded_packages.get(platform)
        if not isinstance(uploaded, dict) or any(
            uploaded.get(uploaded_field) != package[manifest_field]
            for uploaded_field, manifest_field in (
                ('url', 'url'),
                ('sha256', 'sha256'),
                ('size', 'compressed_size'),
            )
        ):
            raise _request_error(f'packages.{platform} 与已上传公共包不一致')

    current = existing.get('engine_release')
    if current == manifest:
        return existing
    if isinstance(current, dict):
        current_sequence = current.get('release_sequence')
        if _is_positive_int(current_sequence) and manifest['release_sequence'] <= current_sequence:
            raise _request_error('release_sequence 不得回退或复用')
    existing['engine_release'] = copy.deepcopy(manifest)
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


async def publish_finance_engine_release(
    db: AsyncSession,
    *,
    pk: int,
    document: bytes,
) -> dict:
    """把已签名 Finance v2 清单写入 catalog，并推送平台配置失效通知。"""
    from backend.app.hasn.crud.crud_hasn_app_catalog import hasn_app_catalog_dao
    from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

    if not document or len(document) > MAX_FINANCE_RELEASE_MANIFEST_BYTES:
        raise _request_error('文件为空或超过 1 MiB')
    try:
        manifest = json.loads(document.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _request_error('文件不是合法 UTF-8 JSON') from exc

    catalog = await hasn_app_catalog_dao.get(db, pk)
    if not catalog:
        raise errors.NotFoundError(msg=f'应用目录 {pk} 不存在')
    if catalog.app_id != 'finance':
        raise _request_error('仅 finance 应用允许发布此清单')
    catalog.config_json = merge_finance_engine_release(catalog.config_json, manifest)
    await db.flush()
    await sync_bump('platform_config', db)
    return catalog.config_json['engine_release']


async def stage_signed_engine_package(
    db: AsyncSession,
    *,
    pk: int,
    os_arch: str,
    version: str,
    data: bytes,
    filename: str,
    expected_sha256: str,
) -> dict:
    """上传 schema v2 待签平台包，返回进入签名正文的云端权威字段。

    本步骤只让对象先在公共桶可达，不修改 ``config_json.engine``，因此在线 daemon 不会看到半套
    发布。对象 key 绑定内容摘要，上传重试幂等；全部平台上传完后由离线发布工具共同签名，再调用
    :func:`publish_signed_engine_manifest` 一次切换权威清单。
    """
    import hashlib

    from backend.app.hasn.crud.crud_hasn_app_catalog import hasn_app_catalog_dao
    from backend.plugin.s3.service.storage_service import StorageService

    if not _SIGNED_ENGINE_PLATFORM_RE.fullmatch(os_arch):
        raise errors.RequestError(msg=f'引擎包平台键无效：{os_arch}')
    if (
        version in {'.', '..'}
        or not _SIGNED_ENGINE_VERSION_RE.fullmatch(version)
    ):
        raise errors.RequestError(msg='引擎包 version 无效')
    if not _SIGNED_ENGINE_SHA256_RE.fullmatch(expected_sha256):
        raise errors.RequestError(msg='引擎包 sha256 必须是 64 位十六进制')
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if expected_sha256.lower() != actual_sha256:
        raise errors.RequestError(
            msg=f'引擎包 sha256 不匹配：客户端 {expected_sha256}，服务端 {actual_sha256}（上传损坏）',
        )
    catalog = await hasn_app_catalog_dao.get(db, pk)
    if not catalog:
        raise errors.NotFoundError(msg=f'应用目录 {pk} 不存在')
    safe_filename = filename
    if (
        not safe_filename
        or '/' in safe_filename
        or '\\' in safe_filename
        or not safe_filename.endswith('.zip')
    ):
        raise errors.RequestError(msg='引擎包 filename 必须是无路径的 .zip 文件名')
    object_key = (
        f'runtime-engine/{catalog.app_id}/{version}/{actual_sha256[:16]}-{safe_filename}'
    )
    ref = await StorageService.upload(
        db,
        data,
        category='film_engine',
        filename=safe_filename,
        content_type='application/zip',
        key=object_key,
    )
    return {
        'key': object_key,
        'url': ref.stable_url,
        'sha256': actual_sha256,
        'compressed_size': len(data),
    }


async def publish_signed_engine_manifest(
    db: AsyncSession,
    *,
    pk: int,
    document: dict,
) -> dict:
    """原子保存已签 schema v2 清单并推送 ``platform_config`` 失效。

    云端不持有也不加载发布公钥；这里只做严格结构/序列/对象归属校验。daemon 收到后仍须用内置
    Ed25519 信任根验签，不能把云端管理员权限当作引擎执行信任。
    """
    from backend.app.hasn.crud.crud_hasn_app_catalog import hasn_app_catalog_dao
    from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

    catalog = await hasn_app_catalog_dao.get(db, pk)
    if not catalog:
        raise errors.NotFoundError(msg=f'应用目录 {pk} 不存在')
    merged = merge_signed_engine_manifest(
        catalog.config_json,
        app_id=catalog.app_id,
        document=document,
    )
    if merged == (catalog.config_json or {}):
        return copy.deepcopy(merged['engine'])
    catalog.config_json = merged
    await db.flush()
    await sync_bump('platform_config', db)
    return copy.deepcopy(merged['engine'])


# ---- 图坊模型签名目录（schema v1）：与引擎清单同构的哑存储发布面 ----
#
# 云端同样不持有、不加载发布公钥；daemon 收到后仍用内置 Ed25519 信任根验签，云端结构校验
# 只用于挡住明显残缺的发布，不能当作执行信任。

_SIGNED_MODEL_DOCUMENT_FIELDS = {'payload', 'signature'}
_SIGNED_MODEL_PAYLOAD_FIELDS = {
    'schema_version',
    'catalog_id',
    'release_sequence',
    'channel',
    'issued_at',
    'expires_at',
    'minimum_daemon_version',
    'key_id',
    'models',
}
_SIGNED_MODEL_RELEASE_FIELDS = {
    'runtime_name',
    'artifact_id',
    'display_name',
    'purposes',
    'license',
    'version',
    'filename',
    'size',
    'sha256',
    'revoked',
    'package',
}
_SIGNED_MODEL_PACKAGE_FIELDS = {
    'key',
    'url',
    'sha256',
    'compressed_size',
    'installed_size',
}
_SIGNED_MODEL_CATALOG_ID = 'imagelab-models'
_SIGNED_MODEL_DEPENDENCY_RE = re.compile(r'^model\.[A-Za-z0-9._-]{1,120}$')
_SIGNED_MODEL_FILENAME_RE = re.compile(r'^[A-Za-z0-9._-]{1,128}\.onnx$')
# 模型 zip 上限 4 GiB，与 daemon `model_artifact.rs` 的 MAX_PACKAGE_BYTES 对齐。
MAX_SIGNED_MODEL_PACKAGE_BYTES = 4 * 1024 * 1024 * 1024
# 签名目录 json 上限 8 MiB；供 api 层做有界读，避免误传模型包时全量读进内存。
MAX_SIGNED_MODEL_CATALOG_BYTES = 8 * 1024 * 1024
_SIGNED_MODEL_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024


def _require_exact_model_fields(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise errors.RequestError(msg=f'图坊模型目录 {label}字段不符合 schema v1')
    return value


def _validate_signed_model_package(
    *,
    app_id: str,
    runtime_name: str,
    version: str,
    package_value: object,
) -> dict:
    package = _require_exact_model_fields(
        package_value,
        _SIGNED_MODEL_PACKAGE_FIELDS,
        f'models.{runtime_name}.package ',
    )
    object_key = package['key']
    expected_prefix = f'runtime-model/{app_id}/{runtime_name}/{version}/'
    if not isinstance(object_key, str) or not object_key.startswith(expected_prefix):
        raise errors.RequestError(msg=f'图坊模型目录 {runtime_name} 的包 key 必须位于 {expected_prefix}')
    url = package['url']
    if not isinstance(url, str) or urlparse(url).scheme not in {'http', 'https'}:
        raise errors.RequestError(msg=f'图坊模型目录 {runtime_name} 的包 url 必须是 http(s)')
    digest = package['sha256']
    if not isinstance(digest, str) or not _SIGNED_ENGINE_SHA256_RE.fullmatch(digest):
        raise errors.RequestError(msg=f'图坊模型目录 {runtime_name} 的包 sha256 必须是 64 位十六进制')
    for field in ('compressed_size', 'installed_size'):
        size = package[field]
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise errors.RequestError(msg=f'图坊模型目录 {runtime_name} 的包 {field} 必须是正整数')
    return package


def _validate_signed_model_release(*, app_id: str, dependency_id: str, release_value: object) -> None:
    if not isinstance(dependency_id, str) or not _SIGNED_MODEL_DEPENDENCY_RE.fullmatch(dependency_id):
        raise errors.RequestError(msg=f'图坊模型目录依赖 ID 无效：{dependency_id}')
    release = _require_exact_model_fields(
        release_value,
        _SIGNED_MODEL_RELEASE_FIELDS,
        f'models.{dependency_id} ',
    )
    runtime_name = release['runtime_name']
    if not isinstance(runtime_name, str) or not _SIGNED_ENGINE_TOKEN_RE.fullmatch(runtime_name):
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 runtime_name 无效')
    # 制品身份必须由 runtime_name 确定性派生，避免同一模型在不同发布里指向不同制品目录。
    expected_artifact_id = f'app.model.{app_id}.{runtime_name}'
    if release['artifact_id'] != expected_artifact_id:
        raise errors.RequestError(
            msg=f'图坊模型目录 {dependency_id} 的 artifact_id 必须为 {expected_artifact_id}',
        )
    display_name = release['display_name']
    if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 128:
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 display_name 无效')
    purposes = release['purposes']
    if (
        not isinstance(purposes, list)
        or not purposes
        or len(purposes) > 32
        or any(not isinstance(item, str) or not _SIGNED_ENGINE_TOKEN_RE.fullmatch(item) for item in purposes)
        or len(set(purposes)) != len(purposes)
    ):
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 purposes 无效')
    license_expression = release['license']
    if not isinstance(license_expression, str) or not license_expression.strip():
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 license 不能为空')
    version = release['version']
    if not isinstance(version, str) or version in {'.', '..'} or not _SIGNED_ENGINE_VERSION_RE.fullmatch(version):
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 version 无效')
    filename = release['filename']
    if not isinstance(filename, str) or not _SIGNED_MODEL_FILENAME_RE.fullmatch(filename):
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 filename 必须是单段 .onnx')
    size = release['size']
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 size 必须是正整数')
    digest = release['sha256']
    if not isinstance(digest, str) or not _SIGNED_ENGINE_SHA256_RE.fullmatch(digest):
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 sha256 必须是 64 位十六进制')
    if not isinstance(release['revoked'], bool):
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 revoked 必须是布尔值')
    package = _validate_signed_model_package(
        app_id=app_id,
        runtime_name=runtime_name,
        version=version,
        package_value=release['package'],
    )
    # 解压后必须精确等于模型文件字节数：daemon 按此判定单文件载荷，不一致即安装失败。
    if package['installed_size'] != size:
        raise errors.RequestError(msg=f'图坊模型目录 {dependency_id} 的 installed_size 必须等于模型 size')


def _validate_signed_model_catalog(*, app_id: str, document: object) -> dict:
    signed = _require_exact_model_fields(document, _SIGNED_MODEL_DOCUMENT_FIELDS, '顶层')
    signature = signed['signature']
    if not isinstance(signature, str) or not _SIGNED_ENGINE_SIGNATURE_RE.fullmatch(signature):
        raise errors.RequestError(msg='图坊模型目录 signature 必须是 128 位 Ed25519 hex')
    payload = _require_exact_model_fields(signed['payload'], _SIGNED_MODEL_PAYLOAD_FIELDS, 'payload ')
    if payload['schema_version'] != 1:
        raise errors.RequestError(msg='图坊模型目录 schema_version 必须为 1')
    if payload['catalog_id'] != _SIGNED_MODEL_CATALOG_ID:
        raise errors.RequestError(msg=f'图坊模型目录 catalog_id 必须为 {_SIGNED_MODEL_CATALOG_ID}')
    sequence = payload['release_sequence']
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence <= 0:
        raise errors.RequestError(msg='图坊模型目录 release_sequence 必须是正整数')
    for field in ('channel', 'key_id'):
        value = payload[field]
        if not isinstance(value, str) or not _SIGNED_ENGINE_TOKEN_RE.fullmatch(value):
            raise errors.RequestError(msg=f'图坊模型目录 {field} 无效')
    minimum_daemon_version = payload['minimum_daemon_version']
    if not isinstance(minimum_daemon_version, str) or not _SIGNED_ENGINE_SEMVER_RE.fullmatch(minimum_daemon_version):
        raise errors.RequestError(msg='图坊模型目录 minimum_daemon_version 无效')
    issued_at = _parse_signed_engine_time(payload['issued_at'], 'issued_at')
    expires_at = _parse_signed_engine_time(payload['expires_at'], 'expires_at')
    if expires_at <= issued_at:
        raise errors.RequestError(msg='图坊模型目录 expires_at 必须晚于 issued_at')
    models = payload['models']
    if not isinstance(models, dict) or not models:
        raise errors.RequestError(msg='图坊模型目录 models 不能为空')
    if len(models) > 64:
        raise errors.RequestError(msg='图坊模型目录 models 超过 64 个上限')
    artifact_ids: set[str] = set()
    for dependency_id, release in models.items():
        _validate_signed_model_release(app_id=app_id, dependency_id=dependency_id, release_value=release)
        artifact_id = release['artifact_id']
        if artifact_id in artifact_ids:
            raise errors.RequestError(msg=f'图坊模型目录制品身份重复：{artifact_id}')
        artifact_ids.add(artifact_id)
    return signed


def merge_signed_model_catalog(config_json: dict | None, *, app_id: str, document: dict) -> dict:
    """严格校验并保存 schema v1 模型签名目录；发布序列单调、同版本异摘要拒绝。

    与 :func:`merge_signed_engine_manifest` 同一套重放与幂等语义：低序列拒绝，相同序列只接受
    逐字节幂等重发；同一模型同版本的 zip 摘要一旦发布不得改写。
    """
    signed = copy.deepcopy(_validate_signed_model_catalog(app_id=app_id, document=document))
    existing = copy.deepcopy(config_json or {})
    current = existing.get('models')
    if (
        isinstance(current, dict)
        and isinstance(current.get('signed_catalog'), dict)
        and isinstance(current['signed_catalog'].get('payload'), dict)
    ):
        previous = current['signed_catalog']
        previous_payload = previous['payload']
        incoming_payload = signed['payload']
        previous_sequence = previous_payload.get('release_sequence')
        incoming_sequence = incoming_payload['release_sequence']
        if isinstance(previous_sequence, int):
            if incoming_sequence < previous_sequence:
                raise errors.RequestError(msg='图坊模型目录发布序列重放')
            if incoming_sequence == previous_sequence:
                if previous != signed:
                    raise errors.RequestError(msg='图坊模型目录相同发布序列内容不一致')
                return existing
        previous_models = previous_payload.get('models') or {}
        for dependency_id, incoming_release in incoming_payload['models'].items():
            previous_release = previous_models.get(dependency_id)
            if (
                isinstance(previous_release, dict)
                and previous_release.get('version') == incoming_release['version']
                and previous_release.get('package', {}).get('sha256') != incoming_release['package']['sha256']
            ):
                raise errors.RequestError(msg=f'图坊模型目录同版本异摘要：{dependency_id}')
    # 只替换 signed_catalog 一项，保留 models 下运营可能维护的其它键。
    models_node = dict(current) if isinstance(current, dict) else {}
    models_node['signed_catalog'] = signed
    existing['models'] = models_node
    return existing


async def stage_signed_model_package(
    db: AsyncSession,
    *,
    pk: int,
    runtime_name: str,
    version: str,
    upload: UploadFile,
) -> dict:
    """两遍流式上传模型 zip 到公共桶，返回进入签名正文的云端权威字段。

    模型包动辄数百 MB 到 1 GB，因此先流式哈希、再流式上传，全程有界内存；远程 I/O 前释放
    数据库事务，避免长事务占住连接。本步骤不修改 ``config_json``，在线 daemon 看不到半套发布。
    """
    import hashlib
    import zipfile

    from backend.app.hasn.crud.crud_hasn_app_catalog import hasn_app_catalog_dao
    from backend.plugin.s3.service.storage_service import StorageService

    if not _SIGNED_ENGINE_TOKEN_RE.fullmatch(runtime_name):
        raise errors.RequestError(msg=f'图坊模型 runtime_name 无效：{runtime_name}')
    if version in {'.', '..'} or not _SIGNED_ENGINE_VERSION_RE.fullmatch(version):
        raise errors.RequestError(msg='图坊模型包 version 无效')
    catalog = await hasn_app_catalog_dao.get(db, pk)
    if not catalog:
        raise errors.NotFoundError(msg=f'应用目录 {pk} 不存在')
    app_id = catalog.app_id

    digest = hashlib.sha256()
    size = 0
    await upload.seek(0)
    while chunk := await upload.read(_SIGNED_MODEL_UPLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > MAX_SIGNED_MODEL_PACKAGE_BYTES:
            raise errors.RequestError(msg=f'图坊模型包超过大小上限 {MAX_SIGNED_MODEL_PACKAGE_BYTES} 字节')
        digest.update(chunk)
    if size == 0:
        raise errors.RequestError(msg='图坊模型包不能为空')
    await upload.seek(0)
    if not zipfile.is_zipfile(upload.file):
        raise errors.RequestError(msg='图坊模型包必须是可读取的 ZIP 文件')
    package_sha256 = digest.hexdigest()
    object_key = f'runtime-model/{app_id}/{runtime_name}/{version}/{package_sha256[:16]}-{runtime_name}.zip'

    storage = await StorageService.get_public_package_storage(db, category='film_engine')
    # 远程 I/O 前释放事务：上传可能持续数分钟，长事务会耗尽连接池。
    await db.rollback()
    await upload.seek(0)
    # 必须走分片上传：单次预签名 PUT 的超时与预签名 TTL 都硬顶 1800 秒且不可续传，
    # GB 级模型包只要吞吐略低就会在最后一刻整体作废。
    reference = await StorageService.upload_public_package_to_storage(
        storage,
        upload.file,
        size=size,
        key=object_key,
        content_type='application/zip',
    )
    # 回读核对：预签名 PUT 返回 2xx 不等于对象完整落地，也不保证落在推算出的 provider key 上。
    # 不核对的话，运维会拿着一份「云端认为成功」的字段去签名，故障要到全网 daemon 校验时才暴露。
    stat = await StorageService.stat_on_storage(storage, object_key=object_key)
    if stat.size != size:
        raise errors.ServerError(msg=f'模型包落地大小与上传不一致：期望 {size}，实际 {stat.size}')
    return {
        'key': object_key,
        'url': reference.stable_url,
        'sha256': package_sha256,
        'compressed_size': size,
    }


async def publish_signed_model_catalog(db: AsyncSession, *, pk: int, document: dict) -> dict:
    """原子保存已签模型目录并推送 ``platform_config`` 失效。"""
    from backend.app.hasn.crud.crud_hasn_app_catalog import hasn_app_catalog_dao
    from backend.app.hasn.service.sync_invalidate_service import bump as sync_bump

    catalog = await hasn_app_catalog_dao.get(db, pk)
    if not catalog:
        raise errors.NotFoundError(msg=f'应用目录 {pk} 不存在')
    merged = merge_signed_model_catalog(catalog.config_json, app_id=catalog.app_id, document=document)
    if merged == (catalog.config_json or {}):
        return copy.deepcopy(merged['models'])
    catalog.config_json = merged
    await db.flush()
    await sync_bump('platform_config', db)
    return copy.deepcopy(merged['models'])


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
    return affected_rows(result)


# ============================ C2：catalog 作为展示权威 ============================


def catalog_to_manifest(cat: HasnAppCatalog, *, registry_app: App | None = None) -> dict:
    """把 catalog 行映射为工作台 manifest（与 ``App.to_manifest`` 同形 + ``icon_asset_uri``）。

    执行契约字段（execution_mode/ui_kind/window_url/window_origin/project_aware/project_required）
    不允许被陈旧 catalog 行改写——迁移期从本地 ``registry_app`` overlay；registry 在 C6 退役后由
    daemon 本地提供（对齐设计 §3 边界「本地 builtin 只保留 launch/执行契约字段」）。
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
        'execution_mode': registry_app.execution_mode if registry_app else cat.execution_mode,
        'ui_kind': registry_app.ui_kind if registry_app else None,
        'window_url': registry_app.window_url if registry_app else None,
        'window_origin': registry_app.window_origin if registry_app else None,
        'project_aware': registry_app.project_aware if registry_app else False,
        'project_required': registry_app.project_required if registry_app else False,
        # APPBETA-2：发布阶段（ga/beta_full/beta_gray）+ 自定义角标（文字+颜色）。
        # 客户端据 release_phase 渲染「内测」标识、据 badge 渲染右上角自定义角标；
        # 灰度门控（beta_gray 未授权 → 锁定卡 + 申请）由 access 字段承载（resolve_app_access）。
        'release_phase': cat.release_phase or 'ga',
        'badge': ({'text': cat.badge_text, 'color': cat.badge_color or None} if cat.badge_text else None),
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


async def resolve_owner_user_id(db: AsyncSession, *, owner_hasn_id: str) -> int | None:
    """owner hasn_id（h_xxx）→ 唤星平台 user_id；无映射返回 None。

    与 ``resolve_owner_hasn_id`` 互为正/反向映射，供跨模块复用（如工作台未处理项聚合对
    走遗留 ``user_id`` 的应用做 hasn_id→user_id 适配）。
    """
    stmt = sa.select(HasnHumans.user_id).where(HasnHumans.hasn_id == owner_hasn_id)
    user_id = (await db.execute(stmt)).scalars().first()
    return int(user_id) if user_id else None


async def owner_effective_tier(db: AsyncSession, *, owner_hasn_id: str) -> str:
    """owner 的**有效订阅档**（实时读，零新增存储；复用 billing UserSubscription）。

    存储的 ``tier`` 字段过期不降级（只 ``status`` 翻 expired，见 credit_service.get_user_credits_info）；
    准入须按日期重算：``status`` 已过期或订阅结束日已过 → 有效档位回落 ``free``。免费档无结束日永不过期。
    """
    user_id = await resolve_owner_user_id(db, owner_hasn_id=owner_hasn_id)
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


async def _member_has_assigned_seat(
    db: AsyncSession, *, enterprise_subject_id: str, app_id: str, member_hasn_id: str | None
) -> bool:
    """该成员是否在该企业该 app 有 assigned 席位（doc04 §6.3 情形 A 席位判定）。

    ``enterprise_subject_id`` 是权益主体 ID（str(enterprise_id)）；``member_hasn_id`` 缺失即无席。
    """
    if not member_hasn_id:
        return False
    try:
        enterprise_id = int(enterprise_subject_id)
    except (TypeError, ValueError):
        return False
    stmt = sa.select(HasnAppSeat.id).where(
        HasnAppSeat.enterprise_id == enterprise_id,
        HasnAppSeat.app_id == app_id,
        HasnAppSeat.member_hasn_id == member_hasn_id,
        HasnAppSeat.status == 'assigned',
    )
    return (await db.execute(stmt)).scalars().first() is not None


def merge_access(owner_access: dict, enterprise_access: dict | None) -> dict:
    """合并 owner 维度与企业维度准入（doc04 §6.6，M1「顺序即优先级 + 自解优先」）。

    - ``allowed = owner OR enterprise``。
    - reason 选取顺序（allowed=False 时）：
      ① 企业 allowed → 用企业结果；② owner allowed → 用 owner 结果；
      ③ 双不通：**自解优先**——owner.reason ∈ {need_purchase, need_upgrade} 先取 owner
        （用户能自购/升级，别推去「找管理员」死路）；否则 enterprise.reason == need_seat_assignment 取企业；
        否则回落 owner.reason（含 need_enterprise_space / disabled）。
    """
    if enterprise_access is None:
        return owner_access
    if enterprise_access.get('allowed'):
        return enterprise_access
    if owner_access.get('allowed'):
        return owner_access
    # 双方都不通：M1 自解优先。
    if owner_access.get('reason') in ('need_purchase', 'need_upgrade'):
        return owner_access
    if enterprise_access.get('reason') == 'need_seat_assignment':
        return enterprise_access
    return owner_access


def check_purchasable_by(catalog: HasnAppCatalog, *, buyer: str) -> None:
    """下单/试用前校验 ``purchasable_by``（doc04 §5/P1-4），拦无意义下单。违反抛 RequestError。

    - buyer='owner'：``purchasable_by='enterprise'``（纯企业应用）→ 拒（个人买了也用不了）。
    - buyer='enterprise'：``purchasable_by='owner'``（纯个人应用）→ 拒。
    - ``both`` 两边都放行；缺省视为 ``owner``（保守默认，与迁移 DEFAULT 一致）。
    """
    mode = catalog.purchasable_by or 'owner'
    if buyer == 'owner' and mode == 'enterprise':
        raise errors.RequestError(msg='该应用仅限企业购买')
    if buyer == 'enterprise' and mode == 'owner':
        raise errors.RequestError(msg='该应用仅限个人购买')


async def resolve_app_access(  # noqa: C901 有意的分支式准入门（status/beta/free/tier/purchase+席位），E2E 覆盖，保持内聚不拆分
    db: AsyncSession,
    *,
    catalog: HasnAppCatalog,
    owner_hasn_id: str,
    subject_type: str = 'owner',
    subject_id: str | None = None,
    member_hasn_id: str | None = None,
) -> dict:
    """统一准入决策函数（设计 §5.2 / doc04 §6.3）。返回 AppAccess dict。

    判定顺序（§5.2）：
      1. status != published → disabled（下架，任何人不可用）
      2. free → allowed/free
      3. tier → owner 有效档位 ≥ min_tier ? allowed/tier_ok : need_upgrade（附 trial_available）
      4. purchase → 有 active 权益 ? allowed/entitled（trial 来源则 trialing）: need_purchase（附 trial_available）

    维度参数（M2，doc04 §4/§7 破「subject_id 硬编码」）：
    - ``subject_id``：权益主体 ID。owner 维度不传（回落 owner_hasn_id，**行为不变**，向后兼容）；
      企业维度传 ``str(enterprise_id)``。
    - ``member_hasn_id``：**仅企业席位制权益**判定用——判「该成员是否有 assigned 席位」（S1，§6.3 情形 A）。
      免费/订阅制（access_type=free/tier，无 seats_total）**不过席位**，approved 成员直接放行。
    """
    subject_id = subject_id or owner_hasn_id

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

    # APPBETA-2：灰度内测门控（在商业化准入之前）。release_phase=beta_gray 的应用
    # 仅「被邀请或申请且通过审批」的主体可见可用；未授权 → 锁定（need_beta / beta_pending），
    # 客户端据此渲染锁定卡 +「申请内测」/「审核中」。beta_full（全量内测）/ ga 不门控，正常走准入。
    if (catalog.release_phase or 'ga') == 'beta_gray':
        beta = await get_beta_access(db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id)
        if beta is None or beta.status != 'approved':
            reason = 'beta_pending' if (beta is not None and beta.status == 'pending') else 'need_beta'
            return _access(allowed=False, reason=reason, requires='beta')
        # 已通过审批 → 落到下方商业化准入（灰度应用仍可叠加 free/tier/purchase）。

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
            # S1：席位闸**只在** purchase 分支且企业席位制权益（seats_total 有值）生效。
            # 企业买了「套餐」但成员没被指派席位 → need_seat_assignment（用户自己解不了，需管理员指派）。
            if subject_type == 'enterprise' and ent.seats_total is not None:
                has_seat = await _member_has_assigned_seat(
                    db, enterprise_subject_id=subject_id, app_id=catalog.app_id, member_hasn_id=member_hasn_id
                )
                if has_seat:
                    return _access(allowed=True, reason='entitled', entitlement_expires_at=ent.expires_at)
                return _access(allowed=False, reason='need_seat_assignment', requires='seat')
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


def purchase_expiry(billing_cycle: str | None) -> datetime | None:
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
    # P1-4：purchasable_by 校验——个人不得试用/购买纯企业应用。
    check_purchasable_by(catalog, buyer='enterprise' if subject_type == 'enterprise' else 'owner')
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
    expires_at: datetime | None = None,
) -> HasnAppEntitlement:
    """写一条 active 权益（购买回调 / admin 授予共用）。已有**有效** active 则幂等返回（不重复发）。

    唯一约束 ``uq_app_entitlement_active`` 保证每主体每 app 至多一条 ``status='active'`` 行。
    到期复购（doc04 §6.4「续费与到期复购」）：``status='active'`` 但 ``expires_at`` 已过的行
    （``sweep_expired_entitlements`` 定时兜底可能尚未跑到）会撞该 partial unique——先把过期行
    翻 ``'expired'`` 让位、再插新行开新周期。**不就地复写旧行**：owner 试用行保留 ``source=trial``
    历史，``_has_used_trial`` 的「试用只能一次」判定才不会被复购冲掉。
    企业席位制的旧 assigned 席位随后由 ``settle_seat_purchase`` re-parent 到新行（席位账目归一）。
    """
    existing = await get_active_entitlement(db, app_id=app_id, subject_type=subject_type, subject_id=subject_id)
    if existing is not None:
        await _post_grant_seed(db, app_id=app_id, subject_type=subject_type, subject_id=subject_id)
        return existing
    stale = (
        await db.execute(
            sa.select(HasnAppEntitlement).where(
                HasnAppEntitlement.app_id == app_id,
                HasnAppEntitlement.subject_type == subject_type,
                HasnAppEntitlement.subject_id == subject_id,
                HasnAppEntitlement.status == 'active',
            )
        )
    ).scalars().first()
    if stale is not None:
        # 走到这里必然「active 但已过期」（有效行已在上方幂等返回）。
        stale.status = 'expired'
        stale.updated_time = timezone.now()
        # 先 flush 让位：同一 flush 里 INSERT 先于 UPDATE 执行，不先落让位会撞 partial unique。
        await db.flush()
    ent = HasnAppEntitlement(
        app_id=app_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source=source,
        status='active',
        order_ref=order_ref,
        granted_at=timezone.now(),
        expires_at=expires_at,
        # MK-4：应用/席位权益也带付费墙通用语言 feature_key（app:<id>，与商品目录 offering 对齐）。
        # 席位用 source=seat/purchase + seats_total 区分，feature_key 统一走 app 前缀族（doc02 §3）。
        feature_key=f'app:{app_id}',
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
    return affected_rows(result) > 0


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


# ============================ APPBETA：灰度内测访问（申请 / 邀请 / 审批 / 门控查询） ============================


async def get_beta_access(
    db: AsyncSession, *, app_id: str, subject_type: str, subject_id: str
) -> HasnAppBetaAccess | None:
    """取某主体对某 app 的灰度内测访问行（唯一约束 (app,subject) 保证至多一行）。"""
    stmt = sa.select(HasnAppBetaAccess).where(
        HasnAppBetaAccess.app_id == app_id,
        HasnAppBetaAccess.subject_type == subject_type,
        HasnAppBetaAccess.subject_id == subject_id,
    )
    return (await db.execute(stmt)).scalars().first()


async def apply_beta(
    db: AsyncSession,
    *,
    catalog: HasnAppCatalog,
    owner_hasn_id: str,
    note: str | None = None,
    subject_type: str = 'owner',
) -> HasnAppBetaAccess:
    """owner 主动申请灰度内测（status→pending 待管理员审批）。

    校验：app 须 published + release_phase=beta_gray（非灰度应用无需申请）。
    幂等（唯一约束 (app,subject)）：已 approved/pending → 原样返回；rejected / 无行 →
    写/重置为 pending（允许被拒后再申请）。
    """
    if catalog.status != 'published':
        raise errors.ForbiddenError(msg='应用未上架')
    if (catalog.release_phase or 'ga') != 'beta_gray':
        raise errors.RequestError(msg='该应用无需申请内测')
    subject_id = owner_hasn_id
    existing = await get_beta_access(db, app_id=catalog.app_id, subject_type=subject_type, subject_id=subject_id)
    if existing is not None:
        if existing.status in ('approved', 'pending'):
            return existing
        # rejected → 允许再申请：重置为 pending
        existing.status = 'pending'
        existing.source = 'apply'
        existing.note = note
        existing.decided_by = None
        existing.decided_at = None
        existing.updated_time = timezone.now()
        await db.flush()
        return existing
    row = HasnAppBetaAccess(
        app_id=catalog.app_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source='apply',
        status='pending',
        note=note,
    )
    db.add(row)
    await db.flush()
    return row


async def invite_beta(
    db: AsyncSession,
    *,
    app_id: str,
    subject_id: str,
    subject_type: str = 'owner',
    decided_by: str | None = None,
    note: str | None = None,
) -> HasnAppBetaAccess:
    """管理员邀请某主体进灰度内测（直接 approved，无需对方申请）。幂等：已有行 → 升为 approved。"""
    existing = await get_beta_access(db, app_id=app_id, subject_type=subject_type, subject_id=subject_id)
    now = timezone.now()
    if existing is not None:
        existing.status = 'approved'
        existing.source = 'invite'
        if note is not None:
            existing.note = note
        existing.decided_by = decided_by
        existing.decided_at = now
        existing.updated_time = now
        await db.flush()
        return existing
    row = HasnAppBetaAccess(
        app_id=app_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source='invite',
        status='approved',
        note=note,
        decided_by=decided_by,
        decided_at=now,
    )
    db.add(row)
    await db.flush()
    return row


async def decide_beta(
    db: AsyncSession, *, pk: int, approve: bool, decided_by: str | None = None, note: str | None = None
) -> HasnAppBetaAccess:
    """管理员审批一条灰度内测申请：approve→approved / 否则 rejected。"""
    row = (await db.execute(sa.select(HasnAppBetaAccess).where(HasnAppBetaAccess.id == pk))).scalars().first()
    if row is None:
        raise errors.NotFoundError(msg='内测申请不存在')
    row.status = 'approved' if approve else 'rejected'
    if note is not None:
        row.note = note
    row.decided_by = decided_by
    row.decided_at = timezone.now()
    row.updated_time = timezone.now()
    await db.flush()
    return row


async def list_beta_access(
    db: AsyncSession, *, app_id: str | None = None, status: str | None = None
) -> list[HasnAppBetaAccess]:
    """管理员列灰度内测访问（可按 app_id / status 过滤），最新在前。"""
    stmt = sa.select(HasnAppBetaAccess)
    if app_id:
        stmt = stmt.where(HasnAppBetaAccess.app_id == app_id)
    if status:
        stmt = stmt.where(HasnAppBetaAccess.status == status)
    stmt = stmt.order_by(HasnAppBetaAccess.id.desc())
    return list((await db.execute(stmt)).scalars().all())
