-- 自媒体创作运营全链路应用 hasn_creator：14 张表（设计 00 §5.2 / 施工 91 §2）
-- 落 schema hasn_creator（ADR-15 应用独立 schema）；PostgreSQL 语法。
-- 全新建（旧 hx_creator_* 孤儿已删，无生产数据，无停机约束）。
-- 双模归属（设计 §5.4 / v3 §6.7）：project 为运营单元根带 owner_scope/user_id/enterprise_id/assignee/assignee_agent_id；
--   子表冗余 owner_scope/user_id/enterprise_id/assignee 便于角色裁剪查询免 join（仿 hasn_growth GE1）。
-- assignee = 负责运营的人 hasn_id（角色裁剪键；personal 模式 = owner_hasn_id，对齐 GrowthScope）；
--   assignee_agent_id = 负责运营的分身 hasn_id（§8.4 主脑 re-bind）。
-- FK：仅同 schema 直接父子（project_id/content_id/account_id）建物理外键；
--   content.topic_id/viral_pattern_id/playbook_id、topic.content_id、project.playbook_id 等为逻辑引用（避环/可空）。
-- 时间字段统一 created_time/updated_time timestamptz；字典字段 COMMENT ON (value:label:color/...) 格式；JSON 用 jsonb。

SET search_path TO hasn_creator, public;

-- ========== 1. playbook 账号打法（内置 + 自定义；先建供 project 逻辑引用） ==========
CREATE TABLE IF NOT EXISTS playbook (
    id bigserial PRIMARY KEY,
    user_id bigint,
    enterprise_id bigint,
    name varchar(100) NOT NULL,
    goal text,
    content_strategy jsonb NOT NULL DEFAULT '{}'::jsonb,
    cadence jsonb NOT NULL DEFAULT '{}'::jsonb,
    tone_guide text,
    red_lines jsonb NOT NULL DEFAULT '[]'::jsonb,
    is_builtin boolean NOT NULL DEFAULT false,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE playbook IS '账号打法（内容策略 + 创作节奏 + 合规红线），内置随 seed + 用户/企业自定义';
COMMENT ON COLUMN playbook.user_id IS '归属主人（可空=内置 playbook）';
COMMENT ON COLUMN playbook.enterprise_id IS '归属企业（可空）';
COMMENT ON COLUMN playbook.content_strategy IS '内容支柱 + 配比建议';
COMMENT ON COLUMN playbook.cadence IS '创作节奏 {frequency,best_time}';
COMMENT ON COLUMN playbook.red_lines IS '合规红线（禁区话题/平台规则要点）';
CREATE INDEX IF NOT EXISTS idx_creator_playbook_user ON playbook (user_id);
CREATE INDEX IF NOT EXISTS idx_creator_playbook_enterprise ON playbook (enterprise_id);
CREATE INDEX IF NOT EXISTS idx_creator_playbook_builtin ON playbook (is_builtin);

-- ========== 2. project 运营单元根（一个「号」的定位） ==========
CREATE TABLE IF NOT EXISTS project (
    id bigserial PRIMARY KEY,
    project_no varchar(40) NOT NULL UNIQUE,
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    assignee_agent_id varchar(64),
    platform_project_id uuid REFERENCES hasn_project.hasn_project(id) ON DELETE SET NULL,
    name varchar(100) NOT NULL,
    description text,
    primary_platform varchar(50),
    pipeline_mode varchar(16) NOT NULL DEFAULT 'semi-auto',
    playbook_id bigint,
    status varchar(16) NOT NULL DEFAULT 'active',
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE project IS '运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度';
COMMENT ON COLUMN project.owner_scope IS '归属模式 (personal:个人:blue/enterprise:企业:purple)';
COMMENT ON COLUMN project.enterprise_id IS '企业 ID（enterprise 模式；personal 为 NULL）';
COMMENT ON COLUMN project.assignee IS '负责运营的人 hasn_id（角色裁剪键；personal=owner_hasn_id）';
COMMENT ON COLUMN project.assignee_agent_id IS '负责运营的分身 hasn_id（§8.4 主脑 re-bind）';
COMMENT ON COLUMN project.platform_project_id IS '挂靠的平台项目 id（独立于创作业务 project_id；仅作跨应用归集视角）';
COMMENT ON COLUMN project.primary_platform IS '主平台 (xiaohongshu:小红书:red/douyin:抖音:gray/wechat_mp:公众号:green/weibo:微博:orange/bilibili:B站:cyan/zhihu:知乎:blue)';
COMMENT ON COLUMN project.pipeline_mode IS '运营自主度 (manual:手动:gray/semi-auto:半自动:blue/auto:自动:green)';
COMMENT ON COLUMN project.playbook_id IS '采用的账号打法（playbook.id 逻辑引用）';
COMMENT ON COLUMN project.status IS '状态 (active:运营中:green/paused:已暂停:orange/archived:已归档:gray)';
CREATE INDEX IF NOT EXISTS idx_creator_project_user_status ON project (user_id, status);
CREATE INDEX IF NOT EXISTS idx_creator_project_owner_scope ON project (user_id, owner_scope);
CREATE INDEX IF NOT EXISTS idx_creator_project_enterprise_assignee ON project (enterprise_id, assignee) WHERE owner_scope = 'enterprise';
CREATE INDEX IF NOT EXISTS idx_creator_project_assignee_agent ON project (assignee_agent_id);
CREATE INDEX IF NOT EXISTS idx_creator_project_platform_project ON project (assignee, platform_project_id) WHERE platform_project_id IS NOT NULL;

-- ========== 3. profile 项目画像（1:1 project） ==========
CREATE TABLE IF NOT EXISTS profile (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL UNIQUE REFERENCES project(id),
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    niche varchar(100),
    sub_niche varchar(100),
    persona text,
    target_audience text,
    tone varchar(50),
    keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
    content_pillars jsonb NOT NULL DEFAULT '[]'::jsonb,
    posting_frequency varchar(50),
    best_posting_time varchar(50),
    style_references jsonb NOT NULL DEFAULT '[]'::jsonb,
    taboo_topics jsonb NOT NULL DEFAULT '[]'::jsonb,
    bio text,
    pillar_weights jsonb NOT NULL DEFAULT '{}'::jsonb,
    pillar_weights_updated_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE profile IS '项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）';
COMMENT ON COLUMN profile.tone IS '调性（轻松幽默/专业严谨/温暖治愈…自由文本）';
COMMENT ON COLUMN profile.content_pillars IS '内容支柱 ["食谱教程","厨房好物","探店"]';
COMMENT ON COLUMN profile.taboo_topics IS '禁区话题（合规红线硬过滤，§12）';
COMMENT ON COLUMN profile.pillar_weights IS '支柱权重（进化核心）：复盘后按数据反馈调整，下次按权重选支柱';
CREATE INDEX IF NOT EXISTS idx_creator_profile_user ON profile (user_id);
CREATE INDEX IF NOT EXISTS idx_creator_profile_enterprise_assignee ON profile (enterprise_id, assignee) WHERE owner_scope = 'enterprise';

-- ========== 4. account 平台账号（1:N project） ==========
CREATE TABLE IF NOT EXISTS account (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL REFERENCES project(id),
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    platform varchar(50) NOT NULL,
    platform_uid varchar(128),
    nickname varchar(100),
    avatar_url text,
    bio text,
    home_url text,
    followers int NOT NULL DEFAULT 0,
    following int NOT NULL DEFAULT 0,
    total_likes int NOT NULL DEFAULT 0,
    total_favorites int NOT NULL DEFAULT 0,
    total_comments int NOT NULL DEFAULT 0,
    total_posts int NOT NULL DEFAULT 0,
    metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics_updated_at timestamptz,
    auth_status varchar(20) NOT NULL DEFAULT 'not_configured',
    is_primary boolean NOT NULL DEFAULT false,
    notes text,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE account IS '平台账号（1:N project）；同一项目多平台真实账号';
COMMENT ON COLUMN account.platform IS '平台 (xiaohongshu:小红书:red/douyin:抖音:gray/wechat_mp:公众号:green/weibo:微博:orange/bilibili:B站:cyan/zhihu:知乎:blue)';
COMMENT ON COLUMN account.auth_status IS '发布授权 (not_configured:未配置:gray/active:已授权:green/expired:已过期:red)';
CREATE INDEX IF NOT EXISTS idx_creator_account_project ON account (project_id);
CREATE INDEX IF NOT EXISTS idx_creator_account_user ON account (user_id);
CREATE INDEX IF NOT EXISTS idx_creator_account_enterprise_assignee ON account (enterprise_id, assignee) WHERE owner_scope = 'enterprise';

-- ========== 5. competitor 竞品账号 ==========
CREATE TABLE IF NOT EXISTS competitor (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL REFERENCES project(id),
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    name varchar(100) NOT NULL,
    platform varchar(50),
    url text,
    follower_count int NOT NULL DEFAULT 0,
    avg_likes int NOT NULL DEFAULT 0,
    content_style text,
    strengths jsonb NOT NULL DEFAULT '[]'::jsonb,
    notes text,
    tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    last_analyzed timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE competitor IS '竞品账号（定位/选题调研输入）';
COMMENT ON COLUMN competitor.platform IS '平台 (xiaohongshu:小红书:red/douyin:抖音:gray/wechat_mp:公众号:green/weibo:微博:orange/bilibili:B站:cyan/zhihu:知乎:blue)';
CREATE INDEX IF NOT EXISTS idx_creator_competitor_project ON competitor (project_id);
CREATE INDEX IF NOT EXISTS idx_creator_competitor_user ON competitor (user_id);

-- ========== 6. content 内容主体（归 project，状态机核心） ==========
CREATE TABLE IF NOT EXISTS content (
    id bigserial PRIMARY KEY,
    content_no varchar(40) NOT NULL UNIQUE,
    project_id bigint NOT NULL REFERENCES project(id),
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    created_by_agent_id varchar(64),
    title varchar(200) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'idea',
    content_tracks varchar(50) NOT NULL DEFAULT 'article',
    pipeline_mode varchar(16),
    target_platforms jsonb NOT NULL DEFAULT '[]'::jsonb,
    topic_id bigint,
    viral_pattern_id bigint,
    playbook_id bigint,
    review_status varchar(20),
    review_note text,
    reviewer_user_id bigint,
    reviewed_at timestamptz,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE content IS '内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核';
COMMENT ON COLUMN content.created_by_agent_id IS '创作分身 hasn_id（审计）';
COMMENT ON COLUMN content.status IS '状态 (idea:选题:gray/researching:调研中:blue/drafting:创作中:cyan/reviewing:待审核:orange/ready:待发布:purple/published:已发布:green/analyzing:数据跟踪:teal/completed:已复盘:green/archived:已归档:gray)';
COMMENT ON COLUMN content.content_tracks IS '形态轨道（可多值逗号）article:图文/tweet:推文/video:短视频脚本';
COMMENT ON COLUMN content.pipeline_mode IS '本篇自主度 (manual:手动:gray/semi-auto:半自动:blue/auto:自动:green)（缺省继承 project）';
COMMENT ON COLUMN content.topic_id IS '来源选题（topic.id 逻辑引用）';
COMMENT ON COLUMN content.viral_pattern_id IS '套用爆款模式（viral_pattern.id 逻辑引用）';
COMMENT ON COLUMN content.review_status IS '审核状态 (pending:待审:orange/approved:通过:green/rejected:打回:red)';
COMMENT ON COLUMN content.review_note IS '主人审核意见（打回时进下次教训）';
CREATE INDEX IF NOT EXISTS idx_creator_content_project_status ON content (project_id, status);
CREATE INDEX IF NOT EXISTS idx_creator_content_user_status ON content (user_id, status);
CREATE INDEX IF NOT EXISTS idx_creator_content_review ON content (review_status);
CREATE INDEX IF NOT EXISTS idx_creator_content_enterprise_assignee ON content (enterprise_id, assignee) WHERE owner_scope = 'enterprise';

-- ========== 7. content_stage 阶段产出 ==========
CREATE TABLE IF NOT EXISTS content_stage (
    id bigserial PRIMARY KEY,
    content_id bigint NOT NULL REFERENCES content(id),
    project_id bigint NOT NULL,
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    stage varchar(30) NOT NULL,
    content_text text,
    asset_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
    status varchar(20) NOT NULL DEFAULT 'draft',
    version int NOT NULL DEFAULT 1,
    source_type varchar(20) NOT NULL DEFAULT 'ai_generated',
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE content_stage IS '阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播';
COMMENT ON COLUMN content_stage.stage IS '阶段 (research:调研:blue/outline:大纲:cyan/first_draft:初稿:orange/final_draft:终稿:purple/cover:封面:green/storyboard:分镜:teal/voiceover:口播:violet)';
COMMENT ON COLUMN content_stage.asset_refs IS '文件产出（封面/配图 hasn://asset/ 引用，落私有桶）';
COMMENT ON COLUMN content_stage.status IS '状态 (draft:草稿:gray/approved:已采用:green/archived:已归档:gray)';
COMMENT ON COLUMN content_stage.source_type IS '来源 (ai_generated:AI生成:violet/human_edited:人工编辑:blue/imported:导入:gray)';
CREATE INDEX IF NOT EXISTS idx_creator_stage_content ON content_stage (content_id, stage);
CREATE INDEX IF NOT EXISTS idx_creator_stage_user ON content_stage (user_id);

-- ========== 8. topic 选题池 ==========
CREATE TABLE IF NOT EXISTS topic (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL REFERENCES project(id),
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    title varchar(200) NOT NULL,
    potential_score real NOT NULL DEFAULT 0,
    heat_index real NOT NULL DEFAULT 0,
    reason text,
    keywords jsonb NOT NULL DEFAULT '[]'::jsonb,
    creative_angles jsonb NOT NULL DEFAULT '[]'::jsonb,
    status smallint NOT NULL DEFAULT 0,
    content_id bigint,
    batch_date varchar(20),
    source_uid varchar(128),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE topic IS '选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过';
COMMENT ON COLUMN topic.status IS '状态 (0:待处理:gray/1:已采纳:green/2:已跳过:red)';
COMMENT ON COLUMN topic.content_id IS '采纳后关联内容（content.id 逻辑引用）';
CREATE INDEX IF NOT EXISTS idx_creator_topic_project_status ON topic (project_id, status);
CREATE INDEX IF NOT EXISTS idx_creator_topic_user ON topic (user_id);

-- ========== 9. draft 草稿箱（灵感快速捕获） ==========
CREATE TABLE IF NOT EXISTS draft (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL REFERENCES project(id),
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    title varchar(200),
    content text,
    media jsonb NOT NULL DEFAULT '[]'::jsonb,
    tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    target_platforms jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE draft IS '草稿箱（灵感快速捕获，轻量独立于正式流水线）';
COMMENT ON COLUMN draft.media IS '媒体引用（hasn://asset/）';
CREATE INDEX IF NOT EXISTS idx_creator_draft_project ON draft (project_id);
CREATE INDEX IF NOT EXISTS idx_creator_draft_user ON draft (user_id);

-- ========== 10. publish 发布记录（content × account） ==========
CREATE TABLE IF NOT EXISTS publish (
    id bigserial PRIMARY KEY,
    content_id bigint NOT NULL REFERENCES content(id),
    account_id bigint NOT NULL REFERENCES account(id),
    project_id bigint NOT NULL,
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    platform varchar(50) NOT NULL,
    method varchar(20) NOT NULL DEFAULT 'manual_assist',
    status varchar(20) NOT NULL DEFAULT 'draft',
    publish_url text,
    publish_note text,
    approval_user_id bigint,
    approved_at timestamptz,
    error_message text,
    published_at timestamptz,
    views int NOT NULL DEFAULT 0,
    likes int NOT NULL DEFAULT 0,
    comments int NOT NULL DEFAULT 0,
    shares int NOT NULL DEFAULT 0,
    favorites int NOT NULL DEFAULT 0,
    new_followers int NOT NULL DEFAULT 0,
    metrics_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics_updated_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE publish IS '发布记录（= content × account：发到某平台账号 + 数据指标）';
COMMENT ON COLUMN publish.method IS '方式 (manual_assist:人工辅助:gray/api_auto:API自动:green)';
COMMENT ON COLUMN publish.status IS '状态 (draft:草稿:gray/pending_review:待审:orange/approved:已通过:blue/publishing:发布中:cyan/published:已发布:green/failed:失败:red)';
COMMENT ON COLUMN publish.publish_url IS '发布链接（主人回填 / 系统回填）';
COMMENT ON COLUMN publish.publish_note IS '给主人看：发布建议（最佳时间/话题标签/置顶评论）';
COMMENT ON COLUMN publish.error_message IS '失败如实回报（零 fake）';
CREATE INDEX IF NOT EXISTS idx_creator_publish_content ON publish (content_id);
CREATE INDEX IF NOT EXISTS idx_creator_publish_account ON publish (account_id);
CREATE INDEX IF NOT EXISTS idx_creator_publish_user_status ON publish (user_id, status);
CREATE INDEX IF NOT EXISTS idx_creator_publish_project ON publish (project_id);
CREATE INDEX IF NOT EXISTS idx_creator_publish_enterprise_assignee ON publish (enterprise_id, assignee) WHERE owner_scope = 'enterprise';

-- ========== 11. media 素材库 ==========
CREATE TABLE IF NOT EXISTS media (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL REFERENCES project(id),
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    type varchar(20) NOT NULL DEFAULT 'image',
    asset_uri text NOT NULL,
    filename varchar(255),
    file_size bigint,
    width int,
    height int,
    duration int,
    thumbnail_uri text,
    tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    description text,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE media IS '素材库；配图/封面/视频/模板（私有桶引用）';
COMMENT ON COLUMN media.type IS '类型 (image:图片:blue/video:视频:purple/audio:音频:orange/template:模板:green)';
COMMENT ON COLUMN media.asset_uri IS '私有桶引用（hasn://asset/）';
CREATE INDEX IF NOT EXISTS idx_creator_media_project ON media (project_id);
CREATE INDEX IF NOT EXISTS idx_creator_media_user ON media (user_id);

-- ========== 12. content_insight 内容洞察（进化沉淀核心 ★新增） ==========
CREATE TABLE IF NOT EXISTS content_insight (
    id bigserial PRIMARY KEY,
    project_id bigint NOT NULL REFERENCES project(id),
    user_id bigint NOT NULL,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    enterprise_id bigint,
    assignee varchar(64),
    created_by_agent_id varchar(64),
    period varchar(20),
    insight_type varchar(24) NOT NULL DEFAULT 'lesson',
    summary text NOT NULL,
    evidence_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    action_taken jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence numeric(3,2) NOT NULL DEFAULT 0.5,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE content_insight IS '内容洞察（复盘结构化结论，进化沉淀核心）';
COMMENT ON COLUMN content_insight.period IS '复盘周期（2026-W24 周报 / content:{id} 单篇）';
COMMENT ON COLUMN content_insight.insight_type IS '类型 (pillar_performance:支柱表现:blue/hook_pattern:钩子套路:purple/timing:发布时间:orange/audience:受众:cyan/lesson:教训:gray)';
COMMENT ON COLUMN content_insight.evidence_json IS '数据证据（哪些 content/publish 的哪些指标支撑）';
COMMENT ON COLUMN content_insight.action_taken IS '已据此采取的动作（调了哪个 pillar_weight / 加了哪条 viral_pattern / 改了 playbook）';
COMMENT ON COLUMN content_insight.confidence IS '置信度（样本量小时低，不轻易大改）';
CREATE INDEX IF NOT EXISTS idx_creator_insight_project ON content_insight (project_id, period);
CREATE INDEX IF NOT EXISTS idx_creator_insight_user ON content_insight (user_id);

-- ========== 13. viral_pattern 爆款模式库 ==========
CREATE TABLE IF NOT EXISTS viral_pattern (
    id bigserial PRIMARY KEY,
    project_id bigint,
    user_id bigint,
    enterprise_id bigint,
    owner_scope varchar(16) NOT NULL DEFAULT 'personal',
    name varchar(200) NOT NULL,
    pattern_type varchar(24) NOT NULL DEFAULT 'hook',
    template text,
    description text,
    example text,
    usage_count int NOT NULL DEFAULT 0,
    success_rate numeric(5,2) NOT NULL DEFAULT 0,
    source varchar(20) NOT NULL DEFAULT 'ai_extracted',
    tags jsonb NOT NULL DEFAULT '[]'::jsonb,
    is_builtin boolean NOT NULL DEFAULT false,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE viral_pattern IS '爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）';
COMMENT ON COLUMN viral_pattern.project_id IS '归属项目（可空=全局通用）';
COMMENT ON COLUMN viral_pattern.pattern_type IS '类型 (hook:钩子:blue/structure:结构:purple/title:标题:orange/cta:行动号召:green)';
COMMENT ON COLUMN viral_pattern.template IS '模板（如「3 步搞定 X」标题模板）';
COMMENT ON COLUMN viral_pattern.source IS '来源 (ai_extracted:AI提炼:violet/manual:手动:blue/builtin:内置:gray)';
CREATE INDEX IF NOT EXISTS idx_creator_pattern_project ON viral_pattern (project_id);
CREATE INDEX IF NOT EXISTS idx_creator_pattern_user ON viral_pattern (user_id);
CREATE INDEX IF NOT EXISTS idx_creator_pattern_type ON viral_pattern (pattern_type);

-- ========== 14. hot_topic 热榜快照（全局） ==========
CREATE TABLE IF NOT EXISTS hot_topic (
    id bigserial PRIMARY KEY,
    platform_id varchar(50) NOT NULL,
    platform_name varchar(100),
    title varchar(300) NOT NULL,
    url text,
    rank int NOT NULL DEFAULT 0,
    heat_score real NOT NULL DEFAULT 0,
    fetch_source varchar(64),
    fetched_at timestamptz,
    batch_date varchar(20) NOT NULL,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_creator_hot_topic_dedupe UNIQUE (platform_id, url, batch_date)
);
COMMENT ON TABLE hot_topic IS '热榜快照（全局，去重，喂选题；可选数据源）';
COMMENT ON COLUMN hot_topic.batch_date IS '批次（去重键：platform_id+url+batch_date）';
CREATE INDEX IF NOT EXISTS idx_creator_hot_topic_platform ON hot_topic (platform_id, batch_date);
