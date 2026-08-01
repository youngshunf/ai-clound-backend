-- 图坊真实派发键由 work dispatch ID 与 output/job ID 组合，长度会稳定超过 64。
-- 当前态和不可变参与记录必须保存同一原值，避免 PostgreSQL 在登记阶段截断或拒绝。
ALTER TABLE public.hasn_artifacts
    ALTER COLUMN dispatch_id TYPE varchar(128);

ALTER TABLE public.hasn_artifact_contributions
    ALTER COLUMN dispatch_id TYPE varchar(128);
