-- =====================================================
-- 知识库封面：kb 加 cover_asset_uri 列（存 hasn://asset/{id}，序列化边界换 CDN 签名 URL，不存直链）。
-- 主人新建库时可上传封面（走 owner 私有桶上传得 hasn://asset），派发分身建库时分身也必须补一张封面
-- （优先素材搜索配图 → 其次生图 → 兜底自画 SVG，都落私有桶得 hasn://asset），列表卡据此展示封面。
-- 幂等：ADD COLUMN IF NOT EXISTS；存量行封面为空（NULL），列表卡回退占位（零行为变化）。
-- 设计事实源：docs/hasn-node设计文档/02-记忆与知识库/知识库AI-Native应用重设计（RAGFlow处理后端）.md
-- =====================================================
ALTER TABLE "hasn_knowledge"."kb" ADD COLUMN IF NOT EXISTS "cover_asset_uri" varchar(512);
COMMENT ON COLUMN "hasn_knowledge"."kb"."cover_asset_uri" IS '封面资产 hasn://asset/（序列化边界换 CDN 签名 URL，不存直链）';
