-- 技能详情：SKILL.md 正文（双语）+ 技能目录文件清单
-- doc13《技能详情正文与文件清单》。
-- body_en/body_zh：SKILL.md frontmatter 之后的 Markdown 正文，按源语言存原文、另一语言存翻译。
-- files：技能目录递归文件清单，JSON 数组 [{path,size}]（仅名称与大小，不含文件内容）。

ALTER TABLE marketplace_skill ADD COLUMN IF NOT EXISTS body_en TEXT;
ALTER TABLE marketplace_skill ADD COLUMN IF NOT EXISTS body_zh TEXT;
ALTER TABLE marketplace_skill ADD COLUMN IF NOT EXISTS files TEXT;

COMMENT ON COLUMN marketplace_skill.body_en IS '英文正文（SKILL.md frontmatter 之后的 Markdown 正文）';
COMMENT ON COLUMN marketplace_skill.body_zh IS '中文正文（SKILL.md frontmatter 之后的 Markdown 正文）';
COMMENT ON COLUMN marketplace_skill.files IS '技能目录文件清单，JSON 数组 [{path,size}]（仅名称与大小，不含内容）';
