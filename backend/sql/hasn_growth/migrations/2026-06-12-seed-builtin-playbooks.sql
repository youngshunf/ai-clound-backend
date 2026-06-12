-- =====================================================
-- 内置获客 playbook 种子（hasn_growth.playbook，实施/91 M5 / 设计 07 §7.5）
-- playbook = 获客打法模板（目标画像 + 触达节奏 cadence + 话术要点 tone_guide + 止损规则 exit_rule）。
-- 内置行：user_id IS NULL + is_builtin=TRUE，对所有 owner 可见（查询口径 is_builtin OR user_id=me，
-- 与 deck.style_profile builtin 同模式）；分身/主人据此把「帮我获客」展开成有节奏的跟进。
-- 幂等：内置 name 上建 partial unique index → ON CONFLICT DO UPDATE（只覆盖定义型字段）。
-- 注：本表无 deleted_time（不软删），内置行靠 (name) WHERE is_builtin AND user_id IS NULL 唯一。
-- =====================================================

-- 幂等键：内置 playbook 的 name 全局唯一（自定义 playbook 不受约束）
CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_playbook_builtin_name
    ON hasn_growth.playbook (name)
    WHERE is_builtin AND user_id IS NULL;

INSERT INTO hasn_growth.playbook
    (user_id, name, enabled, goal, target_profile, cadence, tone_guide, exit_rule, is_builtin)
VALUES
    -- 1) 标准 B2B 获客：通用价值导向打法，中等节奏
    (
        NULL,
        '标准 B2B 获客',
        TRUE,
        '把匹配目标客群、有真实痛点的 B2B 线索，按价值导向节奏培育成商机——先理解再给价值，不推销轰炸。',
        '{"segment":"B2B","fit":["行业匹配","有可解痛点","有可触达渠道"],"target_role":"业务/决策负责人"}'::jsonb,
        '[{"day":0,"channel":"manual_assist","goal":"价值导向首触达破冰（带由头+一个真实价值点，首触达必过主人审批）"},{"day":3,"channel":"manual_assist","goal":"承接上文，提供新洞察/案例，不催单"},{"day":10,"channel":"manual_assist","goal":"再给一个价值点，邀约低门槛下一步"}]'::jsonb,
        '价值导向、顾问式：每次接触给对方一点真实价值（洞察/资源/帮助），简短诚实留出口。禁高频「在吗/考虑好了吗」，禁夸大与虚假紧迫，禁冒充人类。',
        '{"max_silent_rounds":3,"action":"silent"}'::jsonb,
        TRUE
    ),
    -- 2) 高意向快跟：已有明确兴趣信号的客户，紧节奏趁热推进
    (
        NULL,
        '高意向快跟',
        TRUE,
        '对已表达明确兴趣（询价/要方案/主动咨询）的高意向客户，紧节奏快速推进到商机与成交，趁热打铁不拖延。',
        '{"segment":"any","fit":["已有明确意向信号","询价/要方案/要 demo"],"intent":"high"}'::jsonb,
        '[{"day":0,"channel":"manual_assist","goal":"快速响应其诉求，给到对应价值（首触达必过审）"},{"day":2,"channel":"manual_assist","goal":"推进方案/报价/demo，处理异议"},{"day":4,"channel":"manual_assist","goal":"推进决策，争取拍板或约下一步"}]'::jsonb,
        '响应快、专业、不掉链子：紧扣对方诉求给方案，异议先接住再厘清再回应。仍守红线：不假装已发、不越退订、不编造事实、不做「这单稳了」承诺。',
        '{"max_silent_rounds":2,"action":"silent"}'::jsonb,
        TRUE
    ),
    -- 3) 长周期培育：大客户/长决策链，松节奏长期经营
    (
        NULL,
        '长周期培育',
        TRUE,
        '对决策链长、周期久的大客户，松节奏长期培育、持续提供价值、建立信任，等待切换契机成熟，不死缠不轰炸。',
        '{"segment":"enterprise","fit":["决策链长","周期久","现状有替代方案"],"intent":"medium"}'::jsonb,
        '[{"day":0,"channel":"manual_assist","goal":"轻接触建立关系，给一个相关洞察（首触达必过审）"},{"day":14,"channel":"manual_assist","goal":"持续提供行业价值，了解切换契机（合同到期/现方案痛点）"},{"day":28,"channel":"manual_assist","goal":"记录契机时点，到点再推进，期间保持低频价值接触"}]'::jsonb,
        '耐心、长期主义、克制：把客户当长期关系经营，每次接触必带价值且低频，尊重对方节奏。真不急就别硬推，记下切换契机到点再跟进。守全部合规红线。',
        '{"max_silent_rounds":5,"action":"silent"}'::jsonb,
        TRUE
    )
ON CONFLICT (name) WHERE is_builtin AND user_id IS NULL DO UPDATE SET
    enabled        = EXCLUDED.enabled,
    goal           = EXCLUDED.goal,
    target_profile = EXCLUDED.target_profile,
    cadence        = EXCLUDED.cadence,
    tone_guide     = EXCLUDED.tone_guide,
    exit_rule      = EXCLUDED.exit_rule,
    is_builtin     = EXCLUDED.is_builtin,
    updated_time   = now();
