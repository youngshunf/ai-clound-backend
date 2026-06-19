-- =====================================================
-- 内置规划任务目录种子（hasn_task.builtin_catalog，模块 19 规划与目标管理 PLAN-P4e / 设计 §9.2–§9.5）
-- 4 个官方规划内置任务模板：晨间规划 / 晚间复盘 / 每周复盘 / 每日自动排程（可选）。
-- prompt 引用 hasn.plan.*（本地 MCP 工具，经 Agent MCP Key 连本机 daemon 内执行）与 plan-playbook
-- 技能包；幂等 ON CONFLICT(builtin_key)。
--
-- ⚠️ enabled=FALSE（刻意，对齐 growth/creator 先例）：builtin_task_service.list_enabled 只返回
--    enabled=TRUE 行、daemon 据此无条件播种到每个主脑。daily_briefing 是「人人通用」内置任务
--    （enabled=TRUE）；规划任务是 **opt-in 应用能力**，绝不能自动推给所有用户（否则没开通规划 /
--    没绑 planner 分身的人也被塞「晨间规划」cron，且会与 daily_briefing 双重打扰）。故以
--    enabled=FALSE 落定义（catalog_revision 不含禁用行 → 对存量用户零 daemon churn），待主人开通
--    规划应用 / 绑定 planner 分身时按应用激活、实例化。本迁移只落「权威定义」，不改变任何现有
--    用户的任务集。
--
-- 归属（设计 §5.5 / §9.5 闭环）：target_agent_type='planner' ↔ hub 内置分身模板 builtin_key=planner
--    对称匹配，可由 resolve_default_agent_for_app 解析；到点经内置任务体系自身
--    dispatch_work_session_to_agent 派给 planner 分身（§9.5 时间驱动链），分身跑一轮、产卡片回主会话。
--
-- 接续：晨晚简报 / 复盘为读类聚合，无跨轮状态依赖，故不开 continuation（与 daily_briefing 同范式）；
--    plan_daily_autoschedule 写类（建 flex 块）但每日围绕「当天」幂等重排，亦无需接续。本 seed 与
--    growth/creator 一致只承载 builtin_catalog 12 列权威定义，不发明目录新列。
-- =====================================================
INSERT INTO hasn_task.builtin_catalog
    (builtin_key, name, description, schedule_type, schedule_config, skill_bundle,
     system_prompt, enabled, default_enabled, target_agent_type, revision)
VALUES
    -- 1) 晨间规划：cron 每天 08:00
    (
        'plan_morning_briefing',
        '晨间规划',
        '每天早晨给主人一份晨间规划卡：今天的安排（时间线）+ 今天需要你亲为的事 + 分身在帮你的事 + 还没排期的待办。开通规划 / 绑定 planner 分身后按应用激活。',
        'cron',
        '{"expr":"0 8 * * *"}'::jsonb,
        'huanxing/plan-playbook',
        E'你是主人的规划参谋，每天早晨产一份《晨间规划》。先读 plan-playbook 掌握归属分诊（actor）、Motion 排程打法与零 fake 红线；用 hasn-mcp-tools 学工具调用法（hasn.plan.* 是本地工具，走 hasn.local.tool.call，确定性返回、读类无授权门）。\n'
        '1. 算锚点：以主人本地时区「今天零点」的 RFC3339 作 day_start。\n'
        '2. 聚合：hasn.plan.briefing（kind=morning + day_start）拿今天的 timeline / 今天需要你（owner+collab 未结）/ 分身在帮你（agent 自主未结）/ 已完成 / 未排期。\n'
        '3. 写人话简报卡回主会话：强调今天的安排与待排——按时间线讲清上午/下午各做什么，点出今天最该主人亲为的 1–2 件，提醒还没排期的待办「要不要我排进今天空档」。\n'
        '4. 若发现未排期待办较多，可建议主人「我帮你排一下今天」（实际排程走 hasn.plan.schedule，主人点头再排）。\n'
        '零 fake：读不到的数据如实空列，绝不编造待办 / 日程 / 完成情况；面向主人说人话，不暴露工具名 / 报错原文；归属与优先级最终决策权永远在主人。',
        FALSE,
        FALSE,
        'planner',
        1
    ),
    -- 2) 晚间复盘：cron 每天 21:00
    (
        'plan_evening_review',
        '晚间复盘',
        '每天晚上给主人一份晚间复盘卡：今天完成了什么、还剩什么没做、哪些要顺延到明天。轻量收口一天，opt-in。',
        'cron',
        '{"expr":"0 21 * * *"}'::jsonb,
        'huanxing/plan-playbook',
        E'你是主人的规划参谋，每天晚上产一份《晚间复盘》。先读 plan-playbook（简报 / 复盘聚合语义 + 零 fake 红线）。\n'
        '1. 算锚点：以主人本地时区「今天零点」的 RFC3339 作 day_start。\n'
        '2. 聚合：hasn.plan.briefing（kind=evening + day_start）拿今天的 timeline / 已完成 / 未结待办 / 未排期。\n'
        '3. 写人话复盘卡回主会话：强调完成与遗留——今天做完了几件（据真实数据，绝不夸大）、还剩哪些没做、哪些建议顺延到明天。\n'
        '4. 对没做完的事给一句中肯的建议（顺延 / 砍掉 / 拆小），交主人定夺，不替主人拍板。\n'
        '零 fake：完成情况只基于真实数据，读不到的如实标注，绝不编造「已完成」充数；面向主人说人话；决策权永远在主人。',
        FALSE,
        FALSE,
        'planner',
        1
    ),
    -- 3) 每周复盘：cron 每周一 09:00
    (
        'plan_weekly_review',
        '每周复盘',
        '每周一给主人一份上周复盘卡：上周完成 vs 未完、目标进度、习惯坚持情况，帮主人复盘进化、定本周重点。opt-in。',
        'cron',
        '{"expr":"0 9 * * 1"}'::jsonb,
        'huanxing/plan-playbook',
        E'你是主人的规划参谋，每周一帮主人做一次《每周复盘》。先读 plan-playbook（复盘聚合语义 + 零 fake 红线）。\n'
        '1. 算区间：以上一个自然周（上周一零点 ~ 本周一零点）的 RFC3339 作 start / end。\n'
        '2. 聚合：hasn.plan.review（kind=weekly + start + end）拿区间内的 completed / pending + 目标进度 + 习惯坚持情况。\n'
        '3. 写人话复盘卡回主会话：上周做成了什么、哪些目标推进了 / 卡住了、习惯坚持得怎样（据真实数据，绝不夸大成绩）；点出本周该聚焦的 1–3 个重点。\n'
        '4. 若某目标长期无进展，中肯提醒主人是否要调整或拆解（实际拆解走 hasn.plan.decompose，主人点头再做）。\n'
        '零 fake：复盘只基于真实数据，读不到的标「待回填」，绝不编造进度；面向主人说人话；决策权永远在主人。',
        FALSE,
        FALSE,
        'planner',
        1
    ),
    -- 4) 每日自动排程（可选）：cron 每天 06:30（早于晨间规划，让 08:00 的简报反映已排好的一天）
    (
        'plan_daily_autoschedule',
        '每日自动排程',
        '每天清晨把今天还没排期的待办按 Motion 风格自动排进日历空档（固定会议 / 锁定块不动），让主人一睁眼就有排好的一天。最重打扰的一档，默认关，主人想要「全自动排程」才开。',
        'cron',
        '{"expr":"30 6 * * *"}'::jsonb,
        'huanxing/plan-playbook',
        E'你是主人的规划参谋，每天清晨把今天的待办自动排进日历。先读 plan-playbook（Motion 排程打法：固定块 vs 弹性块、锁定、可解释、排不下如实分流、缺时长不臆造）。\n'
        '1. 算锚点：以主人本地时区「今天零点」的 RFC3339 作 day_start。\n'
        '2. 排程：hasn.plan.schedule（day_start + now=当前时间地板，此前不排）——引擎读 events/todos/preference 纯算法求解（高优先 + 早截止 + 高脑力放上午 + 块间缓冲 + 勿排窗口），建 flex 块标待办已排期。排程需 plan:schedule 授权（出厂 Allow，被主人设 Ask 时按审批回路走）。\n'
        '3. 产排程卡回主会话：把「为什么这么排」的推理串讲给主人；排不下的待办如实列入 overflow + 给建议（砍 / 挪明天 / 缩短）；缺 estimated_minutes 的列入 needs_estimate 待主人补——绝不口算、绝不硬塞、绝不臆造时长。\n'
        '4. 锁定块与固定会议保持不动（Motion 不变量）。\n'
        '零 fake：排不下如实分流绝不假装排进去；缺时长待补绝不硬排；面向主人说人话；主人可随时锁定某块或撤销重排。',
        FALSE,
        FALSE,
        'planner',
        1
    )
ON CONFLICT (builtin_key) DO UPDATE SET
    name              = EXCLUDED.name,
    description       = EXCLUDED.description,
    schedule_type     = EXCLUDED.schedule_type,
    schedule_config   = EXCLUDED.schedule_config,
    skill_bundle      = EXCLUDED.skill_bundle,
    system_prompt     = EXCLUDED.system_prompt,
    enabled           = EXCLUDED.enabled,
    default_enabled   = EXCLUDED.default_enabled,
    target_agent_type = EXCLUDED.target_agent_type,
    revision          = EXCLUDED.revision,
    updated_time      = now();
