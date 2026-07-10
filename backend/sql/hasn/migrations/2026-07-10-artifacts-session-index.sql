-- RC-P4/V5（doc31 §2.3，实施/32 RC-P4）：为「工作会话页资源栏」按 session_id 反查产物加索引。
--
-- 资源栏读 `hasn_artifacts WHERE session_id={会话} AND status='active'`（分身在某工作会话产出的
-- deck/网站/短视频等应用资源，经 RC-P8 `record_app_resource_artifact` 登记时带上会话 session_id）。
-- 无索引则该反查在产物量大时全表扫。partial index 仅索引 session_id 非空行（应用资源/工作会话产物
-- 才有值，纯聊天产物 session_id 为空不占索引），既省空间又命中查询谓词。
--
-- 幂等：`IF NOT EXISTS`；与既有 owner/agent/origin_ref 索引正交，不冲突。
CREATE INDEX IF NOT EXISTS idx_hasn_artifacts_session
    ON public.hasn_artifacts (session_id)
    WHERE session_id IS NOT NULL;
