-- =====================================================
-- 内置任务「记忆复盘整理」目录种子（hasn_task.builtin_catalog）
-- 设计事实源：docs/产品与技术/技术设计/02-平台能力/记忆与知识库/归档/2026-08-06-旧记忆与知识库设计/旧域/19-多节点记忆分层与分身自治整理设计.md §9 / D-24
--             自治边界见 §4（分身只整理自己脚下这一片）、工具面见 §7.3、合并见 §5。
--
-- 为什么 target_scope='all_agents'：每台设备的 hasn-node 各持一片记忆权威，**每个分身只整理自己
-- 脚下那一片**（§4.1 权限矩阵）。既有 target_agent_type 只有「绑单个分身/NULL 回退主脑」的单绑语义，
-- 承载不了「本节点每个分身各整理自己那片」，故本条使用新增的 target_scope 广播语义。
-- 云端受 uq_task_owner_builtin_key (owner_id, builtin_key) 唯一索引约束仍只播**一行** task（绑主脑），
-- 扇出发生在本地：各节点 task_scheduler 到期时向本节点每个在线分身各派发一次。
--
-- enabled=TRUE + default_enabled=TRUE（刻意）：记忆复盘是「人人通用」的基础卫生动作，与 daily_briefing
-- 同档；不整理的记忆会越积越脏（过期不撤、矛盾不消、低置信不降档），代价由主人承担。凌晨 03:00 跑，
-- 避开使用高峰。
--
-- target_agent_type=NULL：不绑任何专业内置分身类型——每个分身整理自己那片，与「是哪类专家」无关；
-- 云端播种落在主脑上只是「必须有一行归属」，真正的执行面是本地扇出。
--
-- 幂等：ON CONFLICT (builtin_key) DO UPDATE。改定义须同时抬 revision（daemon 据此提示「可更新」）。
-- 注意 PostgreSQL 语法：jsonb（非 json）。
-- =====================================================
INSERT INTO hasn_task.builtin_catalog
    (builtin_key, name, description, schedule_type, schedule_config, skill_bundle,
     system_prompt, enabled, default_enabled, target_agent_type, target_scope, revision)
VALUES
    (
        'memory_review',
        '记忆复盘整理',
        '每天凌晨让分身回头看一遍自己在这台设备上记下的事实：过期的撤掉、记错的改正、自相矛盾的挑出来；'
        '主脑分身还会把你各台设备上的记忆合并成一份，让所有设备看到的「你」是同一个。',
        'cron',
        '{"expr":"0 3 * * *"}'::jsonb,
        NULL,
        -- 提示词用 dollar-quoting（$prompt$…$prompt$）：内含真实换行，无需 \n 转义。
        -- 注意别用「E'…\n' 接一串普通 '…\n'」的老写法：PostgreSQL 的字符串续行只对普通字面量生效，
        -- 续行里的 \n 会变成字面量反斜杠+n（既有 plan 种子即踩此坑），提示词会带一堆可见的 \n。
        $prompt$你是主人的分身，每天凌晨把自己在**这台设备**上记下的记忆复盘一遍，让它保持准确、不过期、不自相矛盾。

一、先取候选
调 hasn.memory.review 拿本节点自产记忆的待整理候选，共四类：① 很久没被召回过的低置信条目；② 同一个（对象·谓词·作用域）下重复或疑似互相矛盾的一组；③ valid_until 已经过期的时效性事实；④ 缺 rationale、追溯不到当初依据的。没有候选就是没有，直接进第五步如实汇报，不要硬找活干。

二、逐条判断并处理（只动本节点自产片）
- 内容仍对但表述不准 / 置信该升降 / 该补有效期或依据 → hasn.memory.update；
- 已被更新的事实取代 → 先写新事实，再用 hasn.memory.supersede 让旧条退位；
- 已经过期、不再成立 → hasn.memory.withdraw 并写清楚原因（软删，可追溯、可撤销）。
判断要保守：拿不准就留着别动，宁可少改一条，也不要把对的改错——记错比不记更伤主人。

三、遇到「别的设备记的事实是错的」
你只有整理本节点自产记忆的权限，工具会直接拒绝越界的改动。**被拒绝是正常的，不要重试、不要换写法绕**。正当做法是写一条自己的新事实表达异议：hasn.memory.save 写清 rationale（依据与时间）；若你确认那条旧事实当初也是**你自己**记的，带上 supersedes_hint=<旧事实 id>，合并时会按本人纠正快速通道裁决。

四、如果你是主脑分身，再做一次跨设备合并
先确认自己是不是主人的主脑分身（不确定就当作不是）。**是主脑**才调 hasn.memory.merge：把各台设备汇上来的事实去重、消解冲突、重算主人画像，结果回灌到所有设备。**不是主脑就不要调**——你调了它会转成「向主脑发起一次合并请求」，这也不算错，但正常情况下轮不到你做，合并汇到主脑那台设备上串行执行即可。

五、用主人能懂的话汇报
说清楚：合并/去重了哪几条、撤掉了哪条过期的、改正了哪条、还有哪些自相矛盾需要主人拍板确认。讲人话，**不要出现工具名、字段名、报错原文和内部 id**；没做任何改动时就直说「今天没什么要整理的」。

零 fake 红线：读不到候选就如实说没有，**绝不编造任何记忆内容**、绝不虚构「已整理 N 条」充数；某一步失败就如实说明这部分没做成，不要假装做完。最终仲裁权永远在主人。$prompt$,
        TRUE,
        TRUE,
        NULL,
        'all_agents',
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
    target_scope      = EXCLUDED.target_scope,
    revision          = EXCLUDED.revision,
    updated_time      = now();
