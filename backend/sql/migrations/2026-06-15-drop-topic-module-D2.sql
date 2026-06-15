-- new-api 解耦迁移 D2：删除僵尸模块 app/topic（2026-06-15）
--
-- app/topic 是僵尸模块：路由未挂载（不在 app/router.py）、无任何外部 import、
-- 选题能力已被 app/creator 取代。删除其表 sys_topic/sys_industry（public schema）+ 旧菜单。
--
-- 安全性：两表当前 0 行；仅有内部外键（sys_topic→sys_industry、sys_industry 自引用），
-- 无任何外部表引用。幂等：IF EXISTS。
-- 配套：app/topic 模块整目录删除 + beat 移除 daily_topic_recommendation_task + 删 002_topic_menu.sql。

-- 1) 删表（先删依赖方 sys_topic，再删被依赖的 sys_industry）
DROP TABLE IF EXISTS sys_topic;
DROP TABLE IF EXISTS sys_industry;

-- 2) 删僵尸菜单（002_topic_menu.sql 曾建的 /topic 父级 + 两个子项；幂等）
DELETE FROM sys_menu WHERE name IN ('IndustryManage', 'TopicRecommendation');
DELETE FROM sys_menu WHERE name = 'Topic' AND path = '/topic';
