-- AppCollab（doc21 §4.3/§5.4 · 实施 AC-P6）：film（视频生成）catalog 行回填「默认承接分身类型」+「业务提示词」。
-- film 也归「内容运营官（content_operator）」——视频是内容运营的一种产出形态，不另起「视频分身」（AC-P6 福仔拍板）。
-- 列已由 2026-06-19-app-catalog-default-agent-type.sql 建好；本迁移仅回填存量 film 行（INSERT-only seed 跳过已存在行，故需此 UPDATE）。
-- 幂等：仅在 default_agent_type 为空时回填（不覆盖运营改动）。

UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是视频生成应用的执行分身：把主人的创意做成完整的短视频，按脚本→角色设定→分镜→参考图→片段生成→合成的流水线推进；只调用 hasn.film.* 工具就地生成与精修；产出对客可用的成品，零 fake，失败如实报错。'
WHERE app_id = 'film' AND default_agent_type IS NULL;
