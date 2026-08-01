-- 云存储系统服务号存量改名：发通知时 source 未带 display_name，服务号建号时回落成了
-- 英文 ref_id（`owner_storage`），主人消息列表里就显示成一条英文条目。
-- 发送侧已补 display_name='云存储'（owner_storage_maintenance_service），此处修存量行。
UPDATE public.hasn_service_accounts
SET display_name = '云存储',
    updated_time = now()
WHERE kind = 'system'
  AND ref_id = 'owner_storage'
  AND display_name IN ('owner_storage', '');
