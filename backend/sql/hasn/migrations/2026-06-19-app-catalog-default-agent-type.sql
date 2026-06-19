-- AppCollab（doc21 §4.3/§5.4 · 实施 AC-P1）：hasn_app_catalog 加「默认承接分身类型」+「业务提示词模板」。
-- 两列均 nullable；default_agent_type=NULL 回退主脑，work_session_system_prompt=NULL 仅用本次指令。
-- 幂等：IF NOT EXISTS；UPDATE 仅在列为空时回填三应用默认值（不覆盖运营改动）。

ALTER TABLE hasn_app_catalog
    ADD COLUMN IF NOT EXISTS default_agent_type VARCHAR(64),
    ADD COLUMN IF NOT EXISTS work_session_system_prompt TEXT;

COMMENT ON COLUMN hasn_app_catalog.default_agent_type IS
    '打开本应用默认承接的内置 agent 类型键(=marketplace_template.builtin_key)；NULL=回退主脑(AppCollab doc21 §4.3/§7)';
COMMENT ON COLUMN hasn_app_catalog.work_session_system_prompt IS
    '唤起分身时注入 work_session 的应用业务提示词(职责/产出形态/调用工具/零fake)，与本次指令组合(AppCollab doc21 §5.4)';

-- 三个内容类应用同绑「内容运营官(content_operator)」→ 一个分身默认服务三应用（doc21 §7.3）。
UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是演示文稿应用的执行分身：把主人的诉求做成结构清晰、视觉专业的演示文稿，只调用 hasn.deck.* 工具就地生成与精修；产出对客可用的成品，零 fake，失败如实报错。'
WHERE app_id = 'deck' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是设计系统应用的执行分身：产出渲染目标无关的 token 契约 + 组件库，下游一律 var(--token) 消费；只调用 hasn.designsystem.* 工具，零 fake，失败如实报错。'
WHERE app_id = 'designsystem' AND default_agent_type IS NULL;

UPDATE hasn_app_catalog
SET default_agent_type = 'content_operator',
    work_session_system_prompt = '你是内容运营应用的执行分身：围绕账号定位做选题、创作与发布编排，沉淀可复用打法；只调用 hasn.creator.* 工具，产出对客可用的成品，零 fake，失败如实报错。'
WHERE app_id = 'creator' AND default_agent_type IS NULL;
