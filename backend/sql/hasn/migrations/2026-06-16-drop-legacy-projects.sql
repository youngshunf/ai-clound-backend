-- =====================================================
-- 退役遗留 projects 模块（public.projects / public.project_topics）
--
-- 背景：app/projects 是早期"工作区/项目选题"原型，路由虽挂载但无任何内部消费者
--   （仅自身 codegen crud/service 自引用），创作域已用 hasn_creator.project/topic 取代。
--   本次随 app/projects 模块代码一并退役（删模块 + 去 router 接线）。
--
-- ⚠️ 生产执行须经福仔停机窗口授权（与 deck/billing/community/marketplace/workbench
--    schema 切换同一约定）。本机 dev 库已先行 DROP 验证。
--
-- 幂等：IF EXISTS + CASCADE（确认全库无外键指向这两表后再放行；CASCADE 仅兜底）。
-- =====================================================

DROP TABLE IF EXISTS "public"."project_topics" CASCADE;
DROP TABLE IF EXISTS "public"."projects" CASCADE;
