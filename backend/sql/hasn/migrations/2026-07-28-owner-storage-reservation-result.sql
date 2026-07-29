-- 幂等重放必须能返回第一次成功创建的资产，不能重新上传或只返回预占 ID。
ALTER TABLE hasn_storage_reservations
    ADD COLUMN IF NOT EXISTS result_asset_id VARCHAR(40);

COMMENT ON COLUMN hasn_storage_reservations.result_asset_id IS '成功提交后的逻辑资产 ID';
