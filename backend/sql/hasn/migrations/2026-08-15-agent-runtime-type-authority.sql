-- =====================================================
-- 分身运行时大脑类型（runtime_type）补上云端权威列
--
-- 背景（实测缺陷）：创建分身时主人选的大脑（hermes / claude_code / codex）在云端
-- **没有任何落点**——`_merge_agent_create_payload` 里算出的 `runtime_type` 从没传给
-- `register_hasn_agent`，`hasn_agents` 也没有这一列。后果是节点侧的自动绑定无从知道
-- 主人选了什么，只能一律建 hermes 绑定；实测它会在向导那条 codex 绑定还在 probing 时
-- 插队建 hermes 并激活，把 codex 顶成 `replaced_by_new_active`，UI 于是"自动变回唤星
-- Runtime"。
--
-- 本迁移只做一件事：给 `hasn_agents` 加 `runtime_type` 列，作为**主人选定的大脑**的
-- 唯一权威。字段名与节点侧 `agents.runtime_type`、绑定表 `runtime_type` 完全同名
-- （治理契约 R14：同一概念跨仓零字段转换）。
--
-- 语义与取值：
--   'hermes' | 'claude_code' | 'codex'  —— 主人显式选定；
--   NULL                               —— 未指定（本迁移之前创建的存量分身）。
--
-- **存量行刻意不回填**：我们并不知道它们当初选的是什么，凭空写 'hermes' 是在造假。
-- NULL 明确表示"云端没有权威意图"，节点侧对 NULL 维持现有行为（回落 hermes），
-- 因此本迁移对存量分身零行为变化。
--
-- 事实源：docs/产品与技术/技术设计/02-平台能力/Runtime与工具体系/
-- =====================================================

ALTER TABLE "public"."hasn_agents"
  ADD COLUMN IF NOT EXISTS "runtime_type" VARCHAR(30);
COMMENT ON COLUMN "public"."hasn_agents"."runtime_type"
  IS '主人选定的运行时大脑类型 (hermes:唤星Runtime:green/claude_code:Claude Code:purple/codex:Codex:blue)·NULL=未指定(存量行)·节点自动绑定据此选类型·与节点 agents.runtime_type 同名同义';

DO $$
DECLARE
  v_total int;
  v_typed int;
BEGIN
  SELECT count(*), count(runtime_type) INTO v_total, v_typed FROM public.hasn_agents;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'hasn_agents' AND column_name = 'runtime_type'
  ) THEN
    RAISE EXCEPTION 'hasn_agents.runtime_type 建列失败';
  END IF;
  RAISE NOTICE '[改后] hasn_agents 共 % 行，其中已声明 runtime_type 的 % 行（存量行保持 NULL 是预期）', v_total, v_typed;
END $$;
