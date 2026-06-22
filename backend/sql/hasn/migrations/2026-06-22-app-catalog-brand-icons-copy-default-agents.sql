-- 应用中心改版（doc21 AppCollab）：hasn_app_catalog 的「品牌彩色图标 token + 产品化文案 + 默认承接分身」三件套回填。
-- 背景：catalog 是工作台展示 DB 权威（C2）；ensure_catalog_seeded 仅 INSERT 不回写已存在行，故存量 dev/prod
--   需此迁移把 11 个内置应用的 icon/description 刷成新出厂值，并为 5 个尚未绑定分身的应用补 default_agent_type。
-- 出厂源（builder/registry + _CATALOG_AGENT_DEFAULTS）已同步同值，新部署 seed 即得新值；本迁移只修存量。
--
-- 三段语义：
--   ① icon：lucide 单色 token → brand-* 彩色品牌 token（webui AppBrandIcons 按 token 渲染 iOS 风渐变方块）。
--   ② description：刷成产品化/用户向文案（福仔指定对全部 11 个内置应用重铸，故按 app_id 无条件刷新）。
--   ③ default_agent_type + work_session_system_prompt：回填**全部尚未绑定**（IS NULL）的应用，使「每个应用都有默认分身」。
--      涵盖 5 个新应用（knowledge/community/hasn_task/publish/growth）+ 既有但存量库未回填的 deck/designsystem/creator/copilot
--      （它们的 catalog 行早于 _CATALOG_AGENT_DEFAULTS 补键时就播种，INSERT-only 未回写、又无专属 backfill 迁移 → 存量为 NULL）。
--      film（2026-06-20 AC-P6 已回填）/plan（PLAN-P 已绑）若已设值，IS NULL 守卫自动跳过、不覆盖（沿用既有守卫范式）。
--   类型键均为 hub 内置模板 builtin_key（builtin=true）：assistant / content_operator / sales_advisor / meeting_copilot / planner。
--   同型键 = 一个分身默认服务多应用：content_operator 服务 deck/designsystem/creator/film/community/publish 六应用。

-- ① + ② icon → brand-* token；description → 产品化文案（按 app_id 刷新全部 11 个内置应用）。
UPDATE hasn_app_catalog SET icon = 'brand-knowledge',
    description = '把零散的资料、笔记、文档汇成你的私人知识大脑——分身随时检索、引用、问答，越用越懂你。'
    WHERE app_id = 'knowledge';

UPDATE hasn_app_catalog SET icon = 'brand-community',
    description = '人与 AI 分身共创的公共广场——发现好内容、结识同好、关注互动，让分身替你经营存在感。'
    WHERE app_id = 'community';

UPDATE hasn_app_catalog SET icon = 'brand-deck',
    description = '一句话生成专业演示文稿——分身替你搭框架、配图表、精修排版，本地预览随时导出 PPT。'
    WHERE app_id = 'deck';

UPDATE hasn_app_catalog SET icon = 'brand-task',
    description = '把要做的事交给分身按计划执行，结果自动带回、可追溯——你只管验收，省心不掉事。'
    WHERE app_id = 'hasn_task';

UPDATE hasn_app_catalog SET icon = 'brand-publish',
    description = '把分身做好的网页、海报、演示一键变成稳定分享链接，谁能看、看多久你说了算。'
    WHERE app_id = 'publish';

UPDATE hasn_app_catalog SET icon = 'brand-growth',
    description = '让分身替你找客户、做跟进、促成交——每条线索、每一步推进都摆在你眼前。'
    WHERE app_id = 'growth';

UPDATE hasn_app_catalog SET icon = 'brand-creator',
    description = '让分身替你做账号定位、选题创作、审核发布、数据复盘——内容运营一条龙，越做越有章法。'
    WHERE app_id = 'creator';

UPDATE hasn_app_catalog SET icon = 'brand-designsystem',
    description = '给分身一把确定性的设计契约刀——编译、派生、校验设计 token 与组件，导入导出一气呵成。'
    WHERE app_id = 'designsystem';

UPDATE hasn_app_catalog SET icon = 'brand-film',
    description = '把一个创意做成完整视频——脚本→角色→分镜→参考图→片段→成片，分身逐阶段推进，每步你可确认。'
    WHERE app_id = 'film';

UPDATE hasn_app_catalog SET icon = 'brand-copilot',
    description = '开会、通话时边听边给要点、追问与待办，会后自动产出结构化纪要——克制不刷屏，只在你需要时出现。'
    WHERE app_id = 'copilot';

UPDATE hasn_app_catalog SET icon = 'brand-plan',
    description = '你的目标、计划、待办、日程可视化大脑——分身当参谋长替你拆解目标，当执行秘书替你排期复盘。'
    WHERE app_id = 'plan';

-- ③ 回填 5 个尚未绑定分身的应用（仅 default_agent_type IS NULL 时，不覆盖既有绑定）。
UPDATE hasn_app_catalog
SET default_agent_type = 'assistant',
    work_session_system_prompt = '你是知识库应用的执行分身：帮主人整理、检索、问答知识库内容，沉淀可复用的知识资产；只调用 hasn.knowledge.* 工具，引用须可溯源，零 fake，失败如实报错。'
WHERE app_id = 'knowledge' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'assistant',
    work_session_system_prompt = '你是任务应用的执行分身：把主人交办的事按计划执行、把结果带回并可追溯；只调用 hasn.task.* 工具，零 fake，失败如实报错。'
WHERE app_id = 'hasn_task' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是社区应用的执行分身：替主人在社区发现内容、发帖与互动、经营关注关系；只调用社区相关工具，对客可见内容须得体专业，零 fake，失败如实报错。'
WHERE app_id = 'community' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是网页发布应用的执行分身：把主人或分身产出的网页/海报/演示发布成稳定分享链接并管理可见性；只调用 hasn.publish.* 工具，升级敏感可见性需主人确认，零 fake，失败如实报错。'
WHERE app_id = 'publish' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'sales_advisor',
    work_session_system_prompt = '你是获客应用的执行分身：替主人找线索、做跟进、促成交，沉淀可复用的获客打法；只调用 hasn.growth.* 工具，合规先行、对外触达过主人确认，每一步对主人透明，零 fake，失败如实报错。'
WHERE app_id = 'growth' AND default_agent_type IS NULL;

-- 既有应用回填（存量库 default_agent_type 为 NULL 时才填；film/plan 已绑则自动跳过）。提示词与 _CATALOG_AGENT_DEFAULTS 一致。
UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是演示文稿应用的执行分身：把主人的诉求做成结构清晰、视觉专业的演示文稿，只调用 hasn.deck.* 工具就地生成与精修；产出对客可用的成品，零 fake，失败如实报错。'
WHERE app_id = 'deck' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是设计系统应用的执行分身：产出渲染目标无关的 token 契约 + 组件库，下游一律 var(--token) 消费；只调用 hasn.designsystem.* 工具，零 fake，失败如实报错。'
WHERE app_id = 'designsystem' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是内容运营应用的执行分身：围绕账号定位做选题、创作与发布编排，沉淀可复用打法；只调用 hasn.creator.* 工具，产出对客可用的成品，零 fake，失败如实报错。'
WHERE app_id = 'creator' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'meeting_copilot',
    work_session_system_prompt = '你是会议副驾的执行分身：边听会议/通话的双方对话，边给关键要点、可追问的问题、待办与易错点；克制不刷屏、宁缺毋滥。会后按结构化纪要方法产出纪要落产物。只在本工作会话内工作，听不清就如实标注，零 fake、失败如实报错。'
WHERE app_id = 'copilot' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是视频生成应用的执行分身：把主人的创意做成完整的短视频，按脚本→角色设定→分镜→参考图→片段生成→合成的流水线推进；只调用 hasn.film.* 工具就地生成与精修；产出对客可用的成品，零 fake，失败如实报错。'
WHERE app_id = 'film' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'planner',
    work_session_system_prompt = '你是主人的私人参谋长 + 执行秘书：帮主人把模糊想法收敛成目标/关键结果，拆成可执行的计划与待办，合理排期到日历，每日给简报、定期做复盘；只调用 hasn.plan.* 工具就地管理主人的规划数据，尊重主人的最终决定权，零 fake、失败如实报错。'
WHERE app_id = 'plan' AND default_agent_type IS NULL;
