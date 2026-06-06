-- 实施 14-AI-Native应用平台/实施/03 P5：知识库实例/凭据收编完成，删除两套旧表。
-- 前置：P2 数据已迁入 hasn_app_instance/hasn_app_credential(app_id='knowledge')；
--       P3/P4 全部读写已重指新表 + resolver；全仓 grep HasnRagflow 仅剩注释/迁移/脚本。
-- 回滚：删表前已 pg_dump 两表数据留底（test-results/ragflow-migration-backup/）。
DROP TABLE IF EXISTS hasn_ragflow_credential;
DROP TABLE IF EXISTS hasn_ragflow_instance;
