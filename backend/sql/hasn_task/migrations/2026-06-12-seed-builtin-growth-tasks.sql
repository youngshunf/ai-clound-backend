-- =====================================================
-- 内置获客任务/工作流目录种子（hasn_task.builtin_catalog，实施/91 M5 / 设计 07 §7.4）
-- 4 个官方获客内置任务模板：每日获客简报 / 周度获客管线 / 沉默客户唤醒 / 商机跟进提醒。
-- prompt 引用 hasn.growth.* 工具与 growth-playbook 技能包；幂等 ON CONFLICT(builtin_key)。
--
-- ⚠️ enabled=FALSE（刻意）：builtin_task_service.list_enabled 只返回 enabled=TRUE 行、daemon 据此
--    无条件播种到每个主脑。daily_briefing 是「人人通用」内置任务（enabled=TRUE）；获客任务是
--    **opt-in 应用能力**，绝不能自动推给所有用户（否则没装获客的人也被塞「获客简报」cron 任务）。
--    故以 enabled=FALSE 落定义（catalog_revision 不含禁用行 → 对存量用户零 daemon churn），
--    待 M7「获客 playbooks 页」按用户是否采用获客（装 growth-playbook / 从销售顾问模板建号）
--    surfacing + 逐户启用/实例化。本迁移只落「权威定义」，不改变任何现有用户的任务集。
--
-- 「周度获客管线」对齐 §7.3 工作流范式：不新造模板机制——prompt 指引分身用 task-orchestrator
--    的 hasn.workflow.* 建图工具把它展开成 DAG（采集→筛选→触达准备/内容营销→汇总简报）。
-- =====================================================
INSERT INTO hasn_task.builtin_catalog
    (builtin_key, name, description, schedule_type, schedule_config, skill_bundle, system_prompt, enabled, revision)
VALUES
    -- 1) 每日获客简报：cron 每日 09:00
    (
        'growth_daily_briefing',
        '每日获客简报',
        '每天产出一份获客漏斗简报：funnel 报表 + 昨日新线索/回复/待审批触达，提醒主人当天该关注什么。',
        'cron',
        '{"expr":"0 9 * * *"}'::jsonb,
        'huanxing/growth-playbook',
        E'你是主人的获客分身，每天产出一份《每日获客简报》。先读 growth-playbook 掌握打法与合规红线。\n'
        '1. 调 hasn.growth.report.funnel 拿漏斗统计（各阶段客户数、转化率）。\n'
        '2. 扫昨日动态：新增线索/客户、收到的客户回复（customer.timeline 近一日）、待主人审批的触达（outreach 待审批队列）。\n'
        '3. 归纳成一份简报：今天最该关注什么、有几条触达待审批、哪些客户回复了需跟进、哪些商机临近。\n'
        '合规与零 fake：只基于真实数据，读不到的如实标注「暂不可用」不编造；面向主人说人话，不暴露工具名/报错原文。\n'
        '若你的工作台支持简报发布工具则用之，否则把简报作为本轮任务产出并通知主人。绝不伪造进度。',
        FALSE,
        1
    ),
    -- 2) 周度获客管线（§7.3 工作流）：cron 每周一 09:00
    (
        'growth_weekly_pipeline',
        '周度获客管线',
        '每周一跑一遍获客管线：采集新线索→筛选评分 qualify→为本周新 qualified 客户起草首触达（进审批）→产出本周获客简报。批量管线，对齐 §7.3 工作流范式。',
        'cron',
        '{"expr":"0 9 * * 1"}'::jsonb,
        'huanxing/growth-playbook',
        E'你是主人的获客分身，每周一执行一轮获客管线。先读 growth-playbook（漏斗纪律 + 合规红线）。\n'
        '若你具备多分身且装了 task-orchestrator：用 hasn.workflow.* 把本管线展开成一张 DAG 跑（A 采集→B 筛选评分→C1 触达准备/C2 内容营销→D 汇总简报，依赖建成边，整图审批）。\n'
        '单分身则按顺序自己跑：\n'
        '1. 采集：hasn.growth.collect.start 按主人目标客群关键词采新线索（恒落主人私有池），轮询 collect.status。\n'
        '2. 筛选：hasn.growth.lead.search 取高分线索→行业画像调研→lead.qualify 晋级值得的/lead.dismiss 噪声写原因。\n'
        '3. 触达准备：为本周新 qualified 客户逐个起草价值导向首触达→hasn.growth.outreach.send（首触达落 pending_approval，批量进主人审批队列，绝不假装已发）。\n'
        '4. 汇总：hasn.growth.report.funnel 出本周漏斗+待审批清单，通知主人。\n'
        '合规红线全程生效：首触达必过审、控频不轰炸、不冒充人类、不编造客户事实。零 fake：没发说待审批，没据标待确认。',
        FALSE,
        1
    ),
    -- 3) 沉默客户唤醒：interval 每两周（14 天）
    (
        'growth_silent_revive',
        '沉默客户唤醒',
        '每两周扫一遍 lifecycle=silent 的沉默客户，挑有回暖信号的（如官网更新/融资动态）择机起草唤醒触达，把停滞关系重新激活。',
        'interval',
        '{"minutes":20160}'::jsonb,
        'huanxing/growth-playbook',
        E'你是主人的获客分身，每两周做一次沉默客户唤醒。先读 growth-playbook。\n'
        '1. hasn.growth.customer.list 筛 lifecycle=silent 的沉默客户。\n'
        '2. 对每个客户读 customer.timeline 回顾历史，判断有没有回暖信号（行业动态、对方公司近期变化、距上次接触够久值得再试）。\n'
        '3. 仅对有合理唤醒理由的客户：起草一条**带新价值**的唤醒触达（不是简单「在吗」），hasn.growth.outreach.send（过审批），并 update_profile 把生命周期从 silent 转回 following。\n'
        '4. 没有回暖理由的客户保持沉默，不强行打扰——尊重止损纪律，把精力留给有希望的。\n'
        '合规红线：退订客户绝不唤醒、首/再触达过审、不轰炸、不编造唤醒理由。零 fake：挑不出值得唤醒的就如实说本轮无唤醒对象。',
        FALSE,
        1
    ),
    -- 4) 商机跟进提醒：cron 每日 09:00
    (
        'growth_opportunity_reminder',
        '商机跟进提醒',
        '每天扫 expected_close_at 临近或阶段长期停滞的商机，提醒主人或起草推进动作，别让到手的商机因无人跟进而流失。',
        'cron',
        '{"expr":"0 9 * * *"}'::jsonb,
        'huanxing/growth-playbook',
        E'你是主人的获客分身，每天做一次商机跟进体检。先读 growth-playbook（商机阶段判定见 references）。\n'
        '1. hasn.growth.customer.list / 关联商机，找出：预计成交日（expected_close_at）临近的、或卡在某阶段长期无进展（停滞）的商机。\n'
        '2. 对每个需关注的商机读 customer.timeline 判断现状：能推进的→起草下一步动作（如推进 opportunity.update_stage 或起草触达，过审批）；需主人决策的→提醒主人。\n'
        '3. 长期停滞且无回暖的→建议回退阶段或评估转沉默止损，如实告诉主人。\n'
        '合规与零 fake：阶段只反映真实进展不夸大；推进动作守全部红线；没有需关注的商机就如实说今日商机一切正常。',
        FALSE,
        1
    )
ON CONFLICT (builtin_key) DO UPDATE SET
    name            = EXCLUDED.name,
    description     = EXCLUDED.description,
    schedule_type   = EXCLUDED.schedule_type,
    schedule_config = EXCLUDED.schedule_config,
    skill_bundle    = EXCLUDED.skill_bundle,
    system_prompt   = EXCLUDED.system_prompt,
    enabled         = EXCLUDED.enabled,
    revision        = EXCLUDED.revision,
    updated_time    = now();
