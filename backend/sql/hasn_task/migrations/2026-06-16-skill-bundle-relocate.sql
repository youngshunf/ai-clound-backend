-- =====================================================
-- CLEAN-5：hasn_skill_bundle 归位 hasn_task 应用 + 去前缀
--
-- 背景：Skill Bundle（多个 skill 的组合）是 owner 私有任务域资源，原以
--   public.hasn_skill_bundle 落在 app/hasn，但模型/CRUD/service/三 scope API
--   自成一体、仅被 app/hasn 自身 + 任务调度 task_scheduler 消费，属任务域。
--   随 ADR-15 应用化收口，迁入 hasn_task 应用 schema 并去 hasn_ 前缀：
--     public.hasn_skill_bundle  →  hasn_task.skill_bundle
--   代码同步从 app/hasn 归位 app/hasn_task（model/schema/crud/service/api）。
--
-- URL 不变（daemon 依赖）：app/admin 端 `/api/v1/hasn/skill/bundles`、
--   agent 端 `/api/v1/hasn/agent/hasn/skill/bundles`（hasn-node BackendGateway
--   list_skill_bundles 调用）路由仍由 app/hasn 的 router 以原 prefix 装配，
--   仅 import 重指到 app/hasn_task。daemon / webui 零改。
--
-- 数据无损：SET SCHEMA + RENAME 不重建表，原有行（含存量）随表迁移。
-- ⚠️ 生产执行须经福仔停机窗口授权（与其它 schema 切换同一约定）。本机 dev 库已先行执行验证。
--
-- 竞态加固：模型上线后应用 lifespan 的 create_all 可能在本迁移前自动建出**空的**
--   hasn_task.skill_bundle。若真实表仍在 public 且该自动表为空，则先丢弃空表，
--   再把真实表迁移过去（避免 RENAME 撞 DuplicateTable）。
-- 幂等：IF EXISTS 守卫 + 仅当真实源仍在 public 时才动作（已迁移则全 no-op）。
-- =====================================================

DO $$
BEGIN
    IF to_regclass('hasn_task.skill_bundle') IS NOT NULL
       AND to_regclass('public.hasn_skill_bundle') IS NOT NULL
       AND (SELECT count(*) FROM hasn_task.skill_bundle) = 0 THEN
        DROP TABLE hasn_task.skill_bundle;
    END IF;
END $$;

ALTER TABLE IF EXISTS "public"."hasn_skill_bundle" SET SCHEMA "hasn_task";
ALTER TABLE IF EXISTS "hasn_task"."hasn_skill_bundle" RENAME TO "skill_bundle";
