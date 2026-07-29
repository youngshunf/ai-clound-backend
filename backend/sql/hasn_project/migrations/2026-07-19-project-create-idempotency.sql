-- 平台项目创建幂等键：支撑图坊两阶段派发会话 A 在凭据刷新或 daemon 重启后安全重放。
-- 请求键只在同一主人范围内唯一；不同主人可使用相同 launch_trace_id，绝不跨主人复用项目。
ALTER TABLE "hasn_project"."hasn_project"
    ADD COLUMN IF NOT EXISTS "client_request_id" varchar(128);

CREATE UNIQUE INDEX IF NOT EXISTS "uq_hasn_project_owner_client_request"
    ON "hasn_project"."hasn_project" ("owner_id", "client_request_id");

COMMENT ON COLUMN "hasn_project"."hasn_project"."client_request_id"
    IS '创建请求幂等键（主人范围唯一；如两阶段派发 launch_trace_id；可空表示普通非幂等创建）';
