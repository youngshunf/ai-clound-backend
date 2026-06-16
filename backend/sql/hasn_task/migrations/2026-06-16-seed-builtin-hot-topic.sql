-- =====================================================
-- 内置任务目录种子：每日热点创作（daily_hot_topic）— 内容运营官内置任务
-- 内置定时任务体系 §5.3：示范「按 agent 类型绑定 + 默认关」的专业内置任务。
--   target_agent_type='content_operator'（绑内容运营官内置 agent，无则回退主脑）
--   default_enabled=FALSE（首播 enabled=false，需主人手动开启 → 不打扰未运营账号的用户）
--   enabled=TRUE（全局上线，会被 seed_builtin_tasks 播种到每个 owner，但默认关）
-- 产出形态对应 content/头条/YYYY-MM-DD-今日热点创作.md（落本地 content/ 并经飞书 wiki 同步）。
-- 幂等 ON CONFLICT(builtin_key)。
-- =====================================================
INSERT INTO "hasn_task"."builtin_catalog"
    ("builtin_key", "name", "description", "schedule_type", "schedule_config",
     "skill_bundle", "system_prompt", "enabled", "default_enabled", "target_agent_type", "revision")
VALUES (
    'daily_hot_topic',
    '每日热点创作',
    '每天由内容运营官分身结合账号定位扫描当日热点，产出一篇贴合人设的图文/头条创作初稿，存好等主人审核。默认关闭，主人在任务页手动开启后生效。',
    'cron',
    '{"expr":"0 9 * * *"}',
    'huanxing/creator-playbook',
    E'你是主人的「内容运营官」分身，每天为主人产出一篇贴合账号定位的《今日热点创作》初稿——抓住当天值得做的热点，写成可直接发布的成品稿，存好等主人审核。\n\n'
    '【产出方式】\n'
    '- 结合账号画像（定位、内容支柱 pillar、目标人群、语气）扫描当日热点，挑 1 个最契合本账号的选题。\n'
    '- 写成一篇完整的图文/头条创作初稿：标题 + 正文 + 配图建议 + 发布要点；不是选题清单，是能直接发的成品稿。\n'
    '- 用你被授权的创作工具（hasn.creator.*）登记初稿并提交主人审核（manual_assist 人工辅助发布），或把初稿落到本地 content/ 目录等主人查看。\n\n'
    '【说人话 · 面向主人】\n'
    '标题、正文、配图建议等一切文字必须是主人和读者能直接看懂的自然语言：禁止出现工具名（hasn.creator.x）、接口名、报错原文、调用栈、内部 id 等技术词。某来源读不到只说「应用名称 + 状态」（如「热点来源暂不可用」），不贴报错。\n\n'
    '【零 fake 铁律】\n'
    '- 热点 / 数据读不到、工具不可用、未授权 → 如实标注，绝不编造热点、数据或成品稿。宁可今天少产一篇，不可造一篇假的。\n'
    '- 选题必须真实贴合账号定位，不为「凑数」硬蹭无关热点。\n'
    '- 今天确实没有契合的热点 → 如实说明「今天没有特别契合本账号定位的热点，未产稿」，不要硬编。\n\n'
    '【边界】这是「轻打扰」内置任务：你只产初稿、不自动发布；是否采用、何时发布由主人决定。完成后提示主人「今日热点创作初稿已备好，待审核」。',
    TRUE,
    FALSE,
    'content_operator',
    1
)
ON CONFLICT ("builtin_key") DO UPDATE SET
    "name"              = EXCLUDED."name",
    "description"       = EXCLUDED."description",
    "schedule_type"     = EXCLUDED."schedule_type",
    "schedule_config"   = EXCLUDED."schedule_config",
    "skill_bundle"      = EXCLUDED."skill_bundle",
    "system_prompt"     = EXCLUDED."system_prompt",
    "enabled"           = EXCLUDED."enabled",
    "default_enabled"   = EXCLUDED."default_enabled",
    "target_agent_type" = EXCLUDED."target_agent_type",
    "revision"          = EXCLUDED."revision",
    "updated_time"      = now();
