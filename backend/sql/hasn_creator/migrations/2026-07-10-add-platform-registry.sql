-- 创作运营重构 S1：平台目录（Platform Registry，含 URL）
-- 设计事实源：docs/自媒体创作运营/01-创作运营重构设计…md §4（平台目录）+ §7（新表）。
-- 平台改选择制：账号/竞品/建号的 platform 不再自由文本，一律选自本权威目录。
-- 内置 seed 不可删（is_builtin=true）；可由运营扩充。PostgreSQL 语法，落 schema hasn_creator。
-- 注意：本表为只读参考 seed（仅 Agent 工具 platform.list + owner 读端点消费），非 admin CRUD 实体，
--   故按既有 codegen model 范式手工建 model（不跑全量 codegen 以免污染前端仓 + 执行 admin 菜单 SQL）。

SET search_path TO hasn_creator, public;

CREATE TABLE IF NOT EXISTS platform (
    id bigserial PRIMARY KEY,
    key varchar(40) NOT NULL UNIQUE,
    name varchar(50) NOT NULL,
    color varchar(20) NOT NULL DEFAULT 'gray',
    home_url text,
    profile_tpl text,
    metrics_labels jsonb NOT NULL DEFAULT '{}'::jsonb,
    has_public_home boolean NOT NULL DEFAULT true,
    supports_publish boolean NOT NULL DEFAULT false,
    sort int NOT NULL DEFAULT 0,
    is_builtin boolean NOT NULL DEFAULT true,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE platform IS '平台目录（选择制，含主页根 URL/主页模板/指标口径）；账号/竞品/项目的 platform 一律选自此表';
COMMENT ON COLUMN platform.key IS '平台英文 key（xiaohongshu/douyin/...；account.platform/competitor.platform 等落此值）';
COMMENT ON COLUMN platform.name IS '平台中文名（小红书/抖音/...）';
COMMENT ON COLUMN platform.color IS '品牌色（复用字典色 red/gray/orange/cyan/blue/... 或 hex）';
COMMENT ON COLUMN platform.home_url IS '平台主页根 URL（https://www.xiaohongshu.com）';
COMMENT ON COLUMN platform.profile_tpl IS '主页链接模板（{uid} 占位；无则留空，用户直接贴主页 URL）';
COMMENT ON COLUMN platform.metrics_labels IS '该平台指标叫法 {followers,likes,posts,favorites}';
COMMENT ON COLUMN platform.has_public_home IS '是否有公开网页主页（公众号/视频号=false → account.add 的 home_url 必填豁免）';
COMMENT ON COLUMN platform.supports_publish IS '是否支持 api_auto 发布（当前多为 manual_assist）';
COMMENT ON COLUMN platform.is_builtin IS '内置 seed（true 不可删）';

CREATE INDEX IF NOT EXISTS idx_creator_platform_sort ON platform (sort);

-- ========== 内置 seed（首批 10 平台，可运营扩充）==========
-- 公众号(wechat_mp)、视频号(wechat_channels) has_public_home=false（无可贴的公开 web 主页）。
INSERT INTO platform (key, name, color, home_url, profile_tpl, metrics_labels, has_public_home, supports_publish, sort, is_builtin)
VALUES
  ('xiaohongshu', '小红书', 'red', 'https://www.xiaohongshu.com', 'https://www.xiaohongshu.com/user/profile/{uid}',
    '{"followers":"粉丝","likes":"获赞","posts":"笔记","favorites":"收藏"}'::jsonb, true, false, 10, true),
  ('douyin', '抖音', 'gray', 'https://www.douyin.com', 'https://www.douyin.com/user/{uid}',
    '{"followers":"粉丝","likes":"获赞","posts":"作品","favorites":"喜欢"}'::jsonb, true, false, 20, true),
  ('wechat_channels', '视频号', 'green', 'https://channels.weixin.qq.com', NULL,
    '{"followers":"粉丝","likes":"赞","posts":"视频","favorites":"收藏"}'::jsonb, false, false, 30, true),
  ('wechat_mp', '公众号', 'green', 'https://mp.weixin.qq.com', NULL,
    '{"followers":"关注","likes":"在看","posts":"文章","favorites":"收藏"}'::jsonb, false, false, 40, true),
  ('bilibili', 'B站', 'cyan', 'https://www.bilibili.com', 'https://space.bilibili.com/{uid}',
    '{"followers":"粉丝","likes":"点赞","posts":"视频","favorites":"收藏"}'::jsonb, true, false, 50, true),
  ('weibo', '微博', 'orange', 'https://weibo.com', 'https://weibo.com/u/{uid}',
    '{"followers":"粉丝","likes":"赞","posts":"微博","favorites":"收藏"}'::jsonb, true, false, 60, true),
  ('zhihu', '知乎', 'blue', 'https://www.zhihu.com', 'https://www.zhihu.com/people/{uid}',
    '{"followers":"关注者","likes":"赞同","posts":"回答","favorites":"收藏"}'::jsonb, true, false, 70, true),
  ('kuaishou', '快手', 'orange', 'https://www.kuaishou.com', 'https://www.kuaishou.com/profile/{uid}',
    '{"followers":"粉丝","likes":"获赞","posts":"作品","favorites":"收藏"}'::jsonb, true, false, 80, true),
  ('xiaoyuzhou', '小宇宙', 'purple', 'https://www.xiaoyuzhoufm.com', 'https://www.xiaoyuzhoufm.com/podcast/{uid}',
    '{"followers":"订阅","likes":"点赞","posts":"单集","favorites":"收藏"}'::jsonb, true, false, 90, true),
  ('youtube', 'YouTube', 'red', 'https://www.youtube.com', 'https://www.youtube.com/channel/{uid}',
    '{"followers":"订阅者","likes":"赞","posts":"视频","favorites":"收藏"}'::jsonb, true, false, 100, true)
ON CONFLICT (key) DO NOTHING;
