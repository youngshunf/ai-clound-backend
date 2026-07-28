-- 为上传幂等键绑定服务端计算的请求载荷，阻止同键异内容覆盖同一对象。
ALTER TABLE hasn_storage_reservations
    ADD COLUMN IF NOT EXISTS request_fingerprint VARCHAR(64);

COMMENT ON COLUMN hasn_storage_reservations.request_fingerprint
    IS '服务端计算的请求载荷 SHA-256 指纹';
