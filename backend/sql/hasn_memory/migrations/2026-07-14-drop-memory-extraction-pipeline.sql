-- 迁移：退役自动提取管线，DROP 两张提取专用表（doc18 §5.5）。
-- 配套 `docs/产品与技术/技术设计/02-平台能力/记忆与知识库/归档/2026-08-06-旧记忆与知识库设计/旧域/18-记忆写入单源化与全会话画像闭环（退役自动提取·教存教查·聊天记录兜底）.md`。
--
-- 背景（doc18）：记忆写入由「云端独立 LLM 自动提取管线（doc16 C2）」收敛为「分身现场主动
--   hasn.memory.save + 主人事后管理」单一来源。整条 MemoryExtractionService 提取路径退役，
--   其配套的两张表随之作废：
--     hasn_memory.memory_extraction_cursor  提取游标（每 owner 增量水位；worker 内部读写，无 API 面）
--     hasn_memory.extraction_job            提取任务队列镜像（doc04 §11 stub，0 行）
--
-- 保留（不受本迁移影响）：owner_memory / owner_memory_contribution / namespace_revision /
--   episodic_turn / semantic_fact / memory_event —— 皆为工具写入路径或画像/召回所需，继续存活。
--
-- 幂等：IF EXISTS，可重复执行；全新库（从未建过提取表）自动跳过，无副作用。
-- 破坏性：DROP TABLE。两表均为 stub/内部游标（无真实业务数据），无需备份数据；仍建议生产执行前全库备份。
-- 执行：psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f 本文件

DROP TABLE IF EXISTS hasn_memory.memory_extraction_cursor;
DROP TABLE IF EXISTS hasn_memory.extraction_job;
