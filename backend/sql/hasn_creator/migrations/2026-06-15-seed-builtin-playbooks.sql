-- =====================================================
-- 内置账号打法 playbook 种子（hasn_creator.playbook，实施/91 M9 / 设计 00 §7）
-- 3 个官方内置打法：种草号 / 科普号 / 品牌号。is_builtin=TRUE、user_id/enterprise_id 均 NULL，
-- 对所有主人可见，project.playbook_id 逻辑引用；企业开通时 ensure_creator_enterprise_seeded
-- 以这些为模板复制成企业可改副本。
--
-- 幂等：先建 partial unique index（内置打法按 name 唯一）→ ON CONFLICT 幂等 upsert。
-- 内置打法是「权威定义」，重复执行只更新定义、不重复插入；不影响主人/企业自定义 playbook。
-- =====================================================
SET search_path TO hasn_creator, public;

-- partial unique index：仅约束「全局内置打法」（is_builtin AND user_id IS NULL AND enterprise_id IS NULL）
-- 同名唯一；主人/企业自定义 playbook（user_id 或 enterprise_id 非空）不受约束，可与内置同名。
CREATE UNIQUE INDEX IF NOT EXISTS uq_creator_playbook_builtin_name
    ON playbook (name)
    WHERE is_builtin = true AND user_id IS NULL AND enterprise_id IS NULL;

INSERT INTO playbook
    (user_id, enterprise_id, name, goal, content_strategy, cadence, tone_guide, red_lines, is_builtin)
VALUES
    -- 1) 种草号：真实体验种草、引导转化
    (
        NULL, NULL,
        '种草号',
        '通过真实可信的体验种草，建立「值得跟的好物推荐人」人设，引导关注与转化。',
        '{"pillars":["好物测评","使用教程","避坑指南","场景化推荐"],"pillar_weights":{"好物测评":0.35,"使用教程":0.25,"避坑指南":0.2,"场景化推荐":0.2}}'::jsonb,
        '{"frequency":"高频（每周 4-6 篇）","best_time":"晚 19:00-22:00"}'::jsonb,
        '真诚体验、说人话、有用优先；先讲对谁有用、为什么好，再讲怎么买——不硬广不夸大。',
        '["不虚假宣传、不夸大功效（守广告法，不用「最/第一/绝对」等绝对化用语）","真实体验，不编造效果与数据（没据写「待回填」）","商业推广如实标注","不标题党货不对板"]'::jsonb,
        true
    ),
    -- 2) 科普号：专业可信、建立权威
    (
        NULL, NULL,
        '科普号',
        '用专业、准确、易懂的知识内容建立领域权威，靠「学到东西」的价值感涨粉。',
        '{"pillars":["干货知识","概念拆解","常见误区","工具方法"],"pillar_weights":{"干货知识":0.35,"概念拆解":0.25,"常见误区":0.2,"工具方法":0.2}}'::jsonb,
        '{"frequency":"中频（每周 2-4 篇）","best_time":"午 12:00-13:00 / 晚 20:00-22:00"}'::jsonb,
        '专业但不端着、把复杂讲简单；先抛一个反直觉的钩子，再层层讲透，给可落地的方法。',
        '["不编造数据、引用观点/数据标来源（案例真实可复现）","不碰伪科学、不做无依据的功效/医疗/金融承诺","内容准确，拿不准的不强行下结论","不标题党，标题承诺什么就讲什么"]'::jsonb,
        true
    ),
    -- 3) 品牌号：沉淀品牌形象与信任
    (
        NULL, NULL,
        '品牌号',
        '沉淀品牌形象、传递价值主张、经营用户信任，把账号做成品牌的长期资产。',
        '{"pillars":["品牌故事","产品价值","用户案例","行业观点"],"pillar_weights":{"品牌故事":0.3,"产品价值":0.3,"用户案例":0.25,"行业观点":0.15}}'::jsonb,
        '{"frequency":"稳定（每周 2-3 篇）","best_time":"工作日 上午 9:00-11:00"}'::jsonb,
        '稳重有调性、代表品牌发声；措辞克制专业，少口语化营销腔，价值与诚意优先于热度。',
        '["措辞稳、不绝对化用语（守广告法）","涉客户/商业信息守保密","不蹭低俗/争议热点伤品牌人设","真实可信，不编造用户案例与数据"]'::jsonb,
        true
    )
ON CONFLICT (name) WHERE is_builtin = true AND user_id IS NULL AND enterprise_id IS NULL
DO UPDATE SET
    goal             = EXCLUDED.goal,
    content_strategy = EXCLUDED.content_strategy,
    cadence          = EXCLUDED.cadence,
    tone_guide       = EXCLUDED.tone_guide,
    red_lines        = EXCLUDED.red_lines,
    updated_time     = now();
