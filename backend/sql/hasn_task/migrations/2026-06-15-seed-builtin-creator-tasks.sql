-- =====================================================
-- 内置创作运营任务目录种子（hasn_task.builtin_catalog，实施/91 M9 / 设计 00 §8.2）
-- 3 个官方创作内置任务模板：周期运营产稿 / 周期复盘进化 / 每日选题速递（可选）。
-- prompt 引用 hasn.creator.*（云端 MCP 工具）与 creator-playbook 技能包；幂等 ON CONFLICT(builtin_key)。
--
-- ⚠️ enabled=FALSE（刻意，同 growth M5 决策）：builtin_task_service.list_enabled 只返回 enabled=TRUE
--    行、daemon 据此无条件播种到每个主脑。daily_briefing 是「人人通用」内置任务（enabled=TRUE）；
--    创作任务是 **opt-in 应用能力**，绝不能自动推给所有用户（否则没装创作 / 没建项目的人也被塞
--    「运营产稿」cron 任务）。故以 enabled=FALSE 落定义（catalog_revision 不含禁用行 → 对存量用户
--    零 daemon churn），待主人在项目页点「交给分身运营」（设计 §8.4）按项目启用 / 实例化。
--    本迁移只落「权威定义」，不改变任何现有用户的任务集。
--
-- 接续（设计 §8.3）：creator_operate / creator_reflect 设计上为 continuation_enabled=true（上轮
--    output_summary 防注入包裹注入下次 session，R1 防重叠）。builtin_catalog 表与 growth 一致只承载
--    9 列权威定义，不含 continuation_enabled 列——接续由 daemon 物化本地任务时按内置任务语义启用，
--    与 growth_weekly_pipeline 等同范式。本 seed 不发明目录新列。
-- =====================================================
INSERT INTO hasn_task.builtin_catalog
    (builtin_key, name, description, schedule_type, schedule_config, skill_bundle, system_prompt, enabled, revision)
VALUES
    -- 1) 周期运营产稿：interval（目录默认日更 1440 分钟；实际由 profile.posting_frequency 每项目推导一档）
    (
        'creator_operate',
        '周期运营产稿',
        '按账号画像周期性产稿：读画像与数据 → 按 pillar_weights 选题 → 创作（图文/推文/短视频脚本）→ 自检 → 提交主人审核。节奏由 profile.posting_frequency 推导，主人「交给分身运营」启用。',
        'interval',
        '{"minutes":1440}'::jsonb,
        'huanxing/creator-playbook',
        E'你是主人的内容运营官，按节奏跑一轮《运营回路》产稿。先读 creator-playbook 掌握运营回路、复盘回路与合规红线；用 hasn-mcp-tools 学工具调用法（hasn.creator.* 是云端 MCP 工具，经 Agent MCP Key 连云端执行，零本地依赖、不走 daemon）。\n'
        '1. 读定位：hasn.creator.project.get + profile.get 拿账号定位、人设与最新支柱权重（pillar_weights）；report.overview 看近期数据。\n'
        '2. 选题：按 pillar_weights 选内容支柱 → 结合热点/竞品/受众需求 → hasn.creator.topic.suggest 入池（或采纳选题池中已有选题）。\n'
        '3. 创作：hasn.creator.pattern.search 选已验证的爆款钩子 → content.create → stage.save（大纲→稿）→ 配图（hasn.image.generate）；短视频则 stage.save 写分镜 storyboard + 口播稿 voiceover，到脚本级成品。\n'
        '4. 自检：按「审核前自检清单」逐项过（定位匹配/人设一致/合规红线/标题不党/数据真实）。\n'
        '5. 提交：content.update 转 reviewing → hasn.creator.publish.submit 落主人审核队列（绝不自动发布、绝不假装已发）。\n'
        '产出本轮小结（做了什么/为什么这么选/下轮计划）供接续。合规与零 fake：不洗稿不抄袭、不编造数据（没据写「待回填」）、发布必过主人审；面向主人说人话，不暴露工具名/报错原文；决策权永远在主人。',
        FALSE,
        1
    ),
    -- 2) 周期复盘进化：cron 每周一 09:00（可主人调）
    (
        'creator_reflect',
        '周期复盘进化',
        '每周复盘近周期数据：分析支柱/钩子/时间表现 → insight.log 沉淀洞察 + proposed_action → 服务端原子回写 pillar_weights/viral_pattern/playbook，把数据变成下次的提升。启用运营即默认一起开。',
        'cron',
        '{"expr":"0 9 * * 1"}'::jsonb,
        'huanxing/creator-playbook',
        E'你是主人的内容运营官，每周做一次《复盘回路》把数据沉淀为进化。先读 creator-playbook（复盘分析框架 + 进化回写规则）。\n'
        '1. 读数据：hasn.creator.report.overview + publish.list 拿近周期已发布内容的真实数据（播放/互动/完读/涨粉等）。\n'
        '2. 缺数据先回填提醒：人工辅助渠道（小红书/抖音）数据靠主人回填——若发现近周期已发布内容缺数据，推主人「请回填 X 篇数据」提醒卡片，绝不 fake 造数。\n'
        '3. 分析：哪些内容支柱表现好/差、哪些钩子有效、什么发布时间互动高——对比上次复盘结论看趋势（接续注入），不重复造轮子。\n'
        '4. 沉淀回写：hasn.creator.insight.log 写洞察 + proposed_action（pillar_weight_delta 调支柱配比 / new_viral_pattern 提炼新爆款套路 / playbook_patch 修打法）→ 服务端原子回写 profile.pillar_weights / viral_pattern / 本项目 playbook，供 creator_operate 下次 profile.get 自动读到新权重（跨任务进化）。\n'
        '5. 关键洞察推主人。\n'
        '产出本轮复盘小结供接续。合规与零 fake：洞察只基于真实数据，读不到的如实标注；不编造效果；面向主人说人话。',
        FALSE,
        1
    ),
    -- 3) 每日选题速递（可选）：cron 每天 08:30
    (
        'creator_topic_radar',
        '每日选题速递',
        '每天按热点 + 画像产当日选题写入选题池，给主人一张「今日选题」卡片（主人挑了再创作，最轻打扰）。可选，opt-in。',
        'cron',
        '{"expr":"30 8 * * *"}'::jsonb,
        'huanxing/creator-playbook',
        E'你是主人的内容运营官，每天产一批《今日选题》。先读 creator-playbook（选题方法论：对标画像支柱、结合热点、做出差异）。\n'
        '1. 读定位：hasn.creator.project.get + profile.get 拿账号定位与 pillar_weights。\n'
        '2. 产选题：按 pillar_weights 选支柱 + 结合当日热点/竞品 → hasn.creator.topic.suggest 按账号画像产当日选题写入选题池（每条标清支柱归属与价值点）。\n'
        '3. 给主人一张「今日选题」卡片：列今日候选选题 + 推荐理由，主人挑了哪条再走创作（最轻打扰，不自动创作）。\n'
        '合规与零 fake：选题贴账号定位与人设，不蹭低俗/争议热点伤号；产不出合适选题就如实说今日无优质选题，不凑数。',
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
