-- 004: 行业标准化标签字典表（doc08 §4.3 行业标准化；阶段二 2.2）
-- 公共池检索要求 industry 标准化（"LED显示屏" 与 "LED屏" 须归到同一类，否则查池命不中）。
-- 轻量自定义体系（非 GB/T 4754 全量，太重）：code=标准标签，aliases=别名数组（命中即归一）。
-- PostgreSQL 语法；落 schema hasn_growth。codegen: --app hasn_growth --schema hasn_growth（仅取 model）。

SET search_path TO hasn_growth, public;

CREATE TABLE IF NOT EXISTS industry_tag (
    id bigserial PRIMARY KEY,
    code varchar(64) NOT NULL,
    name varchar(100) NOT NULL,
    aliases jsonb NOT NULL DEFAULT '[]'::jsonb,
    parent_code varchar(64),
    sort int NOT NULL DEFAULT 0,
    enabled boolean NOT NULL DEFAULT true,
    meta_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_growth_industry_tag_code UNIQUE (code)
);
COMMENT ON TABLE industry_tag IS '行业标准化标签字典（公共池检索按 code 归一·doc08 §4.3）';
COMMENT ON COLUMN industry_tag.code IS '标准行业标签（稳定标识，公共池按此归一检索）';
COMMENT ON COLUMN industry_tag.name IS '行业中文名';
COMMENT ON COLUMN industry_tag.aliases IS '别名数组（原始行业文本命中任一别名即归一到本 code）';
COMMENT ON COLUMN industry_tag.parent_code IS '父级行业 code（层级，可空；初版扁平）';
COMMENT ON COLUMN industry_tag.sort IS '排序权重';
COMMENT ON COLUMN industry_tag.enabled IS '是否启用';
CREATE INDEX IF NOT EXISTS idx_growth_industry_tag_parent ON industry_tag (parent_code);

-- 初版常见获客行业 seed（幂等：ON CONFLICT 跳过；aliases 含简称/口语/英文）
INSERT INTO industry_tag (code, name, aliases, sort) VALUES
('led_display', 'LED显示屏', '["LED屏","LED大屏","LED显示","显示屏","led显示屏","显示屏厂"]'::jsonb, 10),
('lighting', '照明灯具', '["照明","灯具","LED照明","灯饰","灯具厂"]'::jsonb, 20),
('education', '教育培训', '["教育","培训","培训机构","教培","职业培训","K12","在线教育"]'::jsonb, 30),
('catering', '餐饮', '["餐饮","饭店","餐厅","美食","餐饮店","小吃"]'::jsonb, 40),
('machinery', '机械设备', '["机械","设备","机械设备","工业设备","机械厂"]'::jsonb, 50),
('building_material', '建材', '["建材","建筑材料","装饰材料","建材市场"]'::jsonb, 60),
('electronics', '电子产品', '["电子","电子产品","消费电子","数码","电子厂"]'::jsonb, 70),
('apparel', '服装服饰', '["服装","服饰","服装厂","成衣","女装","男装"]'::jsonb, 80),
('beauty', '美容美发', '["美容","美发","美容院","美甲","医美","美容美发"]'::jsonb, 90),
('logistics', '物流运输', '["物流","运输","货运","快递","供应链","物流公司"]'::jsonb, 100),
('real_estate', '房地产', '["房地产","地产","房产","楼盘","房产中介"]'::jsonb, 110),
('medical', '医疗健康', '["医疗","健康","医院","诊所","大健康","医疗器械"]'::jsonb, 120),
('finance', '金融服务', '["金融","理财","保险","贷款","投资","金融服务"]'::jsonb, 130),
('it_software', 'IT软件', '["软件","IT","信息技术","系统开发","SaaS","软件开发","互联网"]'::jsonb, 140),
('ecommerce', '电子商务', '["电商","电子商务","网店","跨境电商","直播电商"]'::jsonb, 150),
('manufacturing', '制造业', '["制造","工厂","生产","加工","制造业"]'::jsonb, 160),
('auto', '汽车', '["汽车","汽配","4S店","二手车","汽车服务","汽车用品"]'::jsonb, 170),
('agriculture', '农业', '["农业","种植","养殖","农产品","农业科技"]'::jsonb, 180),
('chemical', '化工', '["化工","化学","化工原料","化工厂"]'::jsonb, 190),
('furniture', '家具', '["家具","家居","定制家具","家具厂","办公家具"]'::jsonb, 200),
('printing', '印刷包装', '["印刷","包装","印刷厂","包装厂","印刷包装"]'::jsonb, 210),
('hardware', '五金', '["五金","五金件","紧固件","五金工具"]'::jsonb, 220),
('environmental', '环保', '["环保","节能","环保设备","污水处理","环保科技"]'::jsonb, 230),
('security', '安防', '["安防","监控","门禁","安防设备","安防监控"]'::jsonb, 240),
('advertising', '广告传媒', '["广告","传媒","广告公司","媒体","营销","广告传媒"]'::jsonb, 250),
('tourism', '旅游酒店', '["旅游","酒店","旅行社","民宿","旅游服务"]'::jsonb, 260),
('energy', '能源电力', '["能源","电力","新能源","光伏","太阳能","储能"]'::jsonb, 270),
('instrument', '仪器仪表', '["仪器","仪表","检测设备","测量","仪器仪表"]'::jsonb, 280),
('textile', '纺织面料', '["纺织","面料","布料","纺织厂","纺织品"]'::jsonb, 290),
('plastic', '塑料制品', '["塑料","塑胶","注塑","塑料制品","塑料厂"]'::jsonb, 300),
('metal', '金属材料', '["金属","钢材","有色金属","金属加工","金属材料"]'::jsonb, 310),
('craft', '工艺礼品', '["工艺品","礼品","礼品定制","工艺礼品"]'::jsonb, 320),
('cosmetics', '化妆品', '["化妆品","护肤品","彩妆","化妆品厂"]'::jsonb, 330),
('consulting', '咨询服务', '["咨询","顾问","管理咨询","咨询公司"]'::jsonb, 340),
('exhibition', '会展服务', '["会展","展会","展览","会议","会展服务"]'::jsonb, 350)
ON CONFLICT (code) DO NOTHING;
