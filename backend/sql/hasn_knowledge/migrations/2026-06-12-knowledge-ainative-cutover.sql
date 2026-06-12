-- =====================================================
-- 知识库 AI-Native 重做 P2 cutover（设计 §7.1）
-- 前置：①新模块五表已建（codegen --execute）；②存量文档已经
--   scripts/migrate_huanxing_docs_to_knowledge.py 迁移并验证可检索。
-- =====================================================

-- 1. per-user RAGFlow 凭据退役（凭据只活云端=平台 service key 在 hasn_app_instance.credential_ref；
--    hasn_app_credential 表本身保留给真正 user-scoped 凭据的应用）
DELETE FROM hasn_app_credential WHERE app_id = 'knowledge';

-- 2. knowledge public 实例 transport 翻转：daemon_direct → gateway_internal（云端就地执行，D1）
--    注意：endpoint（RAGFlow 内网地址）/ credential_ref（service key 密文）/ config（default_embd_id）
--    为环境相关运维值，dev 与 prod 各自单独设置（§8），本迁移不写死。
UPDATE hasn_app_instance
SET transport_default = 'gateway_internal', updated_time = now()
WHERE app_id = 'knowledge' AND scope = 'public';

-- 3. app/huanxing 文档子域五表退役删除（D9 吸收为知识库原生文档；分享/协作/autosave 不迁，
--    与网页发布/deck 分享重叠——设计 §10）
DROP TABLE IF EXISTS huanxing_document_autosave CASCADE;
DROP TABLE IF EXISTS huanxing_document_collaborator CASCADE;
DROP TABLE IF EXISTS huanxing_document_version CASCADE;
DROP TABLE IF EXISTS huanxing_document CASCADE;
DROP TABLE IF EXISTS huanxing_document_folder CASCADE;
