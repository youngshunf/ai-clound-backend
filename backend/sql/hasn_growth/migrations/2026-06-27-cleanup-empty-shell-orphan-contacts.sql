-- 统一线索池 slice5：清理「入池守卫之前」残留的空壳线索池行（公司名 AND 联系人名 皆空）。
-- 问题#1 的入池守卫（create_manual_lead / 采集清洗）已从源头拒收空壳，本迁移清理存量。
--
-- 仅删「孤儿空壳」——无任何下游引用（lead_ref/customer/contact_source/export_item/rejected_record）：
--   * 这些 FK 均为 NO ACTION，有引用的空壳会被 DB 拦下，绝不误删用户已认领(lead_ref)/已晋级(customer)
--     /已导出(export_item)/查重锚点(rejected_record.duplicate_contact_id) 的数据；
--   * 孤儿空壳 = 采集落库但从无人认领、又无任何业务派生的纯垃圾池行，删之无损。
-- 全程幂等可重跑（DELETE 条件自洽，重跑命中 0 行）。PostgreSQL 语法，落 schema hasn_growth。

SET search_path TO hasn_growth, public;

DELETE FROM contact c
WHERE coalesce(nullif(btrim(c.company_name), ''), NULL) IS NULL
  AND coalesce(nullif(btrim(c.contact_name), ''), NULL) IS NULL
  AND NOT EXISTS (SELECT 1 FROM lead_ref r WHERE r.lead_contact_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM customer cu WHERE cu.lead_contact_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM contact_source cs WHERE cs.lead_contact_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM export_item ei WHERE ei.lead_contact_id = c.id)
  AND NOT EXISTS (SELECT 1 FROM rejected_record rr WHERE rr.duplicate_contact_id = c.id);
