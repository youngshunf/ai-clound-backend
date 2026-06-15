-- 技能版本内容指纹（doc14《官方技能版本规范与桌面端自动更新设计》§五 B1）。
-- content_hash = sha256(规范化 SKILL.md 全文 + 排序后附带文件指纹)[:16]，**源内容指纹**，
-- 驱动 common_skills_revision（替代恒定 version）；区别于 file_hash（下载 zip 包的指纹）。
-- 对齐 marketplace_template_version 已有的 content_hash 列。
-- 幂等：ADD COLUMN IF NOT EXISTS，可重复执行；表已搬入 hasn_marketplace schema。

ALTER TABLE hasn_marketplace.marketplace_skill_version
  ADD COLUMN IF NOT EXISTS content_hash varchar(64);

COMMENT ON COLUMN hasn_marketplace.marketplace_skill_version.content_hash
  IS '源内容指纹 sha256(规范化SKILL.md+排序文件指纹)，驱动 common_skills_revision；区别于 file_hash(下载包指纹)';
