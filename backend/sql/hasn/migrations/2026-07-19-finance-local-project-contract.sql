-- 金融投研换根本地 Vibe-Trading 后，修复存量应用目录的名称、说明与执行形态。
-- 项目参与档位是代码执行契约，由 registry 投影，不在目录表重复持久化。
UPDATE hasn_app_catalog
SET
    name = '金融投研',
    description = '本地优先的金融投研工作台：行情与宏观、专家团队、策略回测、交易复盘、自选盯盘一体化，研究结果不构成投资建议。',
    execution_mode = 'local_tool',
    updated_time = NOW()
WHERE app_id = 'finance';
