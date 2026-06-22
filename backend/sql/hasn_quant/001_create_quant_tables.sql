-- AI 量化交易引擎接入应用 hasn_quant：8 张表（设计 doc23 §4）
-- 落 schema hasn_quant（ADR-15 应用独立 schema）；PostgreSQL 语法。
-- 全新建（无存量、无停机约束）。cloud-brokered：产品级数据权威在唤星 PG（不变量 #3），
--   引擎服务只持运行态（Redis），不存产品数据。
--
-- 全表公共约定（设计 §4 总原则）：
--   - id bigserial PK + created_time/updated_time timestamptz（来自 fba Base DateTimeMixin，本 SQL 仅建表用）；
--   - owner_hasn_id varchar NOT NULL（行级隔离，建索引）；service 层强制按身份过滤；
--   - 金额/价格/数量用 numeric(28,8)（避免浮点误差，对齐 nautilus fail-fast 精度哲学）；
--   - 枚举落 varchar + CHECK（迁移友好，不用 PG enum）；字典字段 COMMENT ON (value:label:color) 格式；JSON 用 jsonb；
--   - agent_hasn_id 默认取调用分身 JWT（创建带归属资源归属默认取凭证身份，对齐 PLANFIX-6）。
-- 删除语义（§4.9）：策略 archived 软删（留回测 history）；部署 stopped 后保留审计流水（合规留痕）；凭据 revoked 软删。
-- P0–P5（回测研究平台）实际只读写 quant_strategy / quant_backtest_run / quant_venue_credential（owner-only）；
--   quant_deployment/position/order/fill/pnl_snapshot 是 P6+（实盘）表，本期一并建 schema（仅建表，不接实盘执行）。

SET search_path TO hasn_quant, public;

-- ========== 4.1 quant_strategy — 策略定义（AI 生成/迭代的 Strategy 代码） ==========
CREATE TABLE IF NOT EXISTS quant_strategy (
    id bigserial PRIMARY KEY,
    owner_hasn_id varchar(64) NOT NULL,
    agent_hasn_id varchar(64),
    name varchar(120) NOT NULL,
    description text,
    code text NOT NULL,
    strategy_class varchar(120) NOT NULL,
    builtin_strategy varchar(60),
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    instrument_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    venue varchar(40),
    status varchar(16) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'backtested', 'deployed', 'archived')),
    version int NOT NULL DEFAULT 1,
    latest_backtest_id bigint,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE quant_strategy IS '量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）';
COMMENT ON COLUMN quant_strategy.owner_hasn_id IS '归属主人 hasn_id（行级隔离键）';
COMMENT ON COLUMN quant_strategy.agent_hasn_id IS '作者分身 hasn_id（创建带归属资源默认取凭证身份，PLANFIX-6）';
COMMENT ON COLUMN quant_strategy.code IS 'Python Strategy 子类源码（沙箱执行，AI 生成=RCE 面）';
COMMENT ON COLUMN quant_strategy.strategy_class IS '入口类名（供引擎装配；与 <class>Config 约定成对）';
COMMENT ON COLUMN quant_strategy.builtin_strategy IS '内置策略键（如 ema_cross_long_only；设了则用内置不读 code）';
COMMENT ON COLUMN quant_strategy.params IS '策略参数（fast_ema/slow_ema/trade_size…）';
COMMENT ON COLUMN quant_strategy.instrument_ids IS '标的列表（["ETHUSDT.BINANCE"]）';
COMMENT ON COLUMN quant_strategy.venue IS '目标场所（BINANCE/IB/…；回测可空）';
COMMENT ON COLUMN quant_strategy.status IS '状态 (draft:草稿:gray/backtested:已回测:blue/deployed:已部署:green/archived:已归档:gray)';
COMMENT ON COLUMN quant_strategy.version IS '版本号（每次保存自增，保留迭代 history）';
COMMENT ON COLUMN quant_strategy.latest_backtest_id IS '最近回测 id（冗余，列表展示最近绩效）';
CREATE INDEX IF NOT EXISTS idx_quant_strategy_owner_status ON quant_strategy (owner_hasn_id, status);
CREATE INDEX IF NOT EXISTS idx_quant_strategy_agent ON quant_strategy (agent_hasn_id);

-- ========== 4.2 quant_backtest_run — 回测任务 + 绩效 ==========
CREATE TABLE IF NOT EXISTS quant_backtest_run (
    id bigserial PRIMARY KEY,
    strategy_id bigint NOT NULL REFERENCES quant_strategy(id),
    owner_hasn_id varchar(64) NOT NULL,
    agent_hasn_id varchar(64),
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    dataset varchar(60),
    data_source varchar(40),
    data_start timestamptz,
    data_end timestamptz,
    status varchar(16) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    metrics jsonb,
    equity_curve jsonb,
    equity_curve_asset_uri varchar(512),
    report_asset_uri varchar(512),
    engine_job_id varchar(80),
    error text,
    duration_secs numeric(12, 3),
    started_at timestamptz,
    finished_at timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE quant_backtest_run IS '回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）';
COMMENT ON COLUMN quant_backtest_run.owner_hasn_id IS '归属主人 hasn_id（行级隔离）';
COMMENT ON COLUMN quant_backtest_run.params IS '本次回测覆盖参数（快照，不回指策略当前值）';
COMMENT ON COLUMN quant_backtest_run.dataset IS '回测数据集键（synthetic-oscillator-eth…；本期合成确定性数据）';
COMMENT ON COLUMN quant_backtest_run.data_source IS '数据源（databento/tardis/catalog/synthetic…）';
COMMENT ON COLUMN quant_backtest_run.status IS '状态 (queued:排队:gray/running:运行中:blue/succeeded:成功:green/failed:失败:red)';
COMMENT ON COLUMN quant_backtest_run.metrics IS '绩效 {sharpe,sortino,max_drawdown,total_return,win_rate,profit_factor,trades_count,fills_count…}';
COMMENT ON COLUMN quant_backtest_run.equity_curve IS '净值曲线点序列（UI 画线；大数据集落桶 equity_curve_asset_uri）';
COMMENT ON COLUMN quant_backtest_run.equity_curve_asset_uri IS '净值曲线产物（私有桶 hasn://asset/…）';
COMMENT ON COLUMN quant_backtest_run.report_asset_uri IS '完整报告产物（私有桶）';
COMMENT ON COLUMN quant_backtest_run.engine_job_id IS '引擎侧 job 标识（云端轮询用）';
COMMENT ON COLUMN quant_backtest_run.error IS '失败真实错误（透传，零 fake）';
COMMENT ON COLUMN quant_backtest_run.duration_secs IS '引擎回测耗时（秒）';
CREATE INDEX IF NOT EXISTS idx_quant_backtest_strategy ON quant_backtest_run (strategy_id, created_time DESC);
CREATE INDEX IF NOT EXISTS idx_quant_backtest_status ON quant_backtest_run (status);
CREATE INDEX IF NOT EXISTS idx_quant_backtest_owner ON quant_backtest_run (owner_hasn_id);

-- ========== 4.3 quant_deployment — 实盘/模拟盘部署（P6+；本期仅建表，不接实盘执行） ==========
CREATE TABLE IF NOT EXISTS quant_deployment (
    id bigserial PRIMARY KEY,
    strategy_id bigint NOT NULL REFERENCES quant_strategy(id),
    owner_hasn_id varchar(64) NOT NULL,
    agent_hasn_id varchar(64),
    venue varchar(40) NOT NULL,
    credential_id bigint,
    environment varchar(8) NOT NULL CHECK (environment IN ('paper', 'live')),
    status varchar(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'paused', 'stopped', 'faulted')),
    risk_limits jsonb NOT NULL DEFAULT '{}'::jsonb,
    engine_node_id varchar(80),
    last_heartbeat_at timestamptz,
    deployed_at timestamptz,
    stopped_at timestamptz,
    stop_reason varchar(16),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE quant_deployment IS '实盘/模拟盘部署（P6+ 真钱强闸；本期仅建 schema，不接实盘执行）';
COMMENT ON COLUMN quant_deployment.owner_hasn_id IS '归属主人 hasn_id（行级隔离）';
COMMENT ON COLUMN quant_deployment.credential_id IS 'venue 凭据 id（paper 可空；live 必填，service 层校验）';
COMMENT ON COLUMN quant_deployment.environment IS '环境 (paper:模拟盘:blue/live:实盘:red)';
COMMENT ON COLUMN quant_deployment.status IS '状态 (pending:待启:gray/running:运行中:green/paused:已暂停:orange/stopped:已停止:gray/faulted:故障:red)';
COMMENT ON COLUMN quant_deployment.risk_limits IS '风控限额 {max_notional,max_position,max_order_rate,daily_loss_limit}';
COMMENT ON COLUMN quant_deployment.engine_node_id IS '引擎侧长驻进程标识';
COMMENT ON COLUMN quant_deployment.stop_reason IS '停止原因 (manual:手动:gray/risk_breach:风控触发:red/fault:故障:red/revoked:凭据撤销:orange)';
CREATE INDEX IF NOT EXISTS idx_quant_deployment_owner_status ON quant_deployment (owner_hasn_id, status);
CREATE INDEX IF NOT EXISTS idx_quant_deployment_strategy ON quant_deployment (strategy_id);

-- ========== 4.4 quant_position — 持仓快照（事件回流落库；P6+） ==========
CREATE TABLE IF NOT EXISTS quant_position (
    id bigserial PRIMARY KEY,
    deployment_id bigint NOT NULL REFERENCES quant_deployment(id),
    owner_hasn_id varchar(64) NOT NULL,
    instrument_id varchar(60) NOT NULL,
    quantity numeric(28, 8) NOT NULL DEFAULT 0,
    avg_px numeric(28, 8),
    unrealized_pnl numeric(28, 8),
    realized_pnl numeric(28, 8),
    snapshot_at timestamptz NOT NULL DEFAULT now(),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_quant_position UNIQUE (deployment_id, instrument_id)
);
COMMENT ON TABLE quant_position IS '持仓快照（事件回流落库，最新快照 upsert；P6+）';
COMMENT ON COLUMN quant_position.owner_hasn_id IS '归属主人 hasn_id（冗余，行级隔离免 join）';
COMMENT ON COLUMN quant_position.quantity IS '持仓量（负=空头）';
COMMENT ON COLUMN quant_position.avg_px IS '持仓均价';
COMMENT ON COLUMN quant_position.unrealized_pnl IS '浮动盈亏';
COMMENT ON COLUMN quant_position.realized_pnl IS '已实现盈亏';
CREATE INDEX IF NOT EXISTS idx_quant_position_deployment ON quant_position (deployment_id);
CREATE INDEX IF NOT EXISTS idx_quant_position_owner ON quant_position (owner_hasn_id);

-- ========== 4.5 quant_order — 订单审计流水（P6+） ==========
CREATE TABLE IF NOT EXISTS quant_order (
    id bigserial PRIMARY KEY,
    deployment_id bigint NOT NULL REFERENCES quant_deployment(id),
    owner_hasn_id varchar(64) NOT NULL,
    client_order_id varchar(80) NOT NULL,
    venue_order_id varchar(80),
    instrument_id varchar(60) NOT NULL,
    side varchar(8) NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type varchar(16) NOT NULL CHECK (order_type IN ('market', 'limit', 'stop', 'stop_limit', 'trailing')),
    qty numeric(28, 8) NOT NULL,
    price numeric(28, 8),
    status varchar(20) NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'accepted', 'partially_filled', 'filled', 'canceled', 'rejected', 'expired')),
    ts_submitted timestamptz,
    ts_last_event timestamptz,
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_quant_order UNIQUE (deployment_id, client_order_id)
);
COMMENT ON TABLE quant_order IS '订单审计流水（事件回流幂等；P6+）';
COMMENT ON COLUMN quant_order.owner_hasn_id IS '归属主人 hasn_id（行级隔离）';
COMMENT ON COLUMN quant_order.client_order_id IS '引擎侧幂等键';
COMMENT ON COLUMN quant_order.side IS '方向 (buy:买:green/sell:卖:red)';
COMMENT ON COLUMN quant_order.order_type IS '订单类型 (market:市价/limit:限价/stop:止损/stop_limit:止损限价/trailing:跟踪)';
COMMENT ON COLUMN quant_order.status IS '状态 (submitted:已提交/accepted:已接受/partially_filled:部分成交/filled:全部成交:green/canceled:已撤:gray/rejected:已拒:red/expired:已过期:gray)';
CREATE INDEX IF NOT EXISTS idx_quant_order_deployment ON quant_order (deployment_id);
CREATE INDEX IF NOT EXISTS idx_quant_order_owner ON quant_order (owner_hasn_id);

-- ========== 4.6 quant_fill — 成交审计流水（P6+） ==========
CREATE TABLE IF NOT EXISTS quant_fill (
    id bigserial PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES quant_order(id),
    deployment_id bigint NOT NULL REFERENCES quant_deployment(id),
    owner_hasn_id varchar(64) NOT NULL,
    trade_id varchar(80) NOT NULL,
    qty numeric(28, 8) NOT NULL,
    px numeric(28, 8) NOT NULL,
    commission numeric(28, 8),
    liquidity_side varchar(8) CHECK (liquidity_side IN ('maker', 'taker')),
    ts timestamptz NOT NULL DEFAULT now(),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_quant_fill UNIQUE (deployment_id, trade_id)
);
COMMENT ON TABLE quant_fill IS '成交审计流水（事件回流幂等；P6+）';
COMMENT ON COLUMN quant_fill.owner_hasn_id IS '归属主人 hasn_id（行级隔离）';
COMMENT ON COLUMN quant_fill.trade_id IS '场所成交 ID';
COMMENT ON COLUMN quant_fill.liquidity_side IS '流动性方向 (maker:挂单/taker:吃单)';
CREATE INDEX IF NOT EXISTS idx_quant_fill_order ON quant_fill (order_id);
CREATE INDEX IF NOT EXISTS idx_quant_fill_deployment ON quant_fill (deployment_id);
CREATE INDEX IF NOT EXISTS idx_quant_fill_owner ON quant_fill (owner_hasn_id);

-- ========== 4.7 quant_pnl_snapshot — 净值/盈亏时序（UI 画曲线；P6+） ==========
CREATE TABLE IF NOT EXISTS quant_pnl_snapshot (
    id bigserial PRIMARY KEY,
    deployment_id bigint NOT NULL REFERENCES quant_deployment(id),
    owner_hasn_id varchar(64) NOT NULL,
    ts timestamptz NOT NULL DEFAULT now(),
    balance numeric(28, 8),
    equity numeric(28, 8),
    realized_pnl numeric(28, 8),
    unrealized_pnl numeric(28, 8),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now()
);
COMMENT ON TABLE quant_pnl_snapshot IS '净值/盈亏时序（UI 画曲线；引擎节流写回，避免高频灌库；P6+）';
COMMENT ON COLUMN quant_pnl_snapshot.owner_hasn_id IS '归属主人 hasn_id（行级隔离）';
COMMENT ON COLUMN quant_pnl_snapshot.equity IS '权益（含浮盈）';
CREATE INDEX IF NOT EXISTS idx_quant_pnl_deployment_ts ON quant_pnl_snapshot (deployment_id, ts);
CREATE INDEX IF NOT EXISTS idx_quant_pnl_owner ON quant_pnl_snapshot (owner_hasn_id);

-- ========== 4.8 quant_venue_credential — venue 凭据托管（BYO 加密；owner-only UI；P6+ 用） ==========
CREATE TABLE IF NOT EXISTS quant_venue_credential (
    id bigserial PRIMARY KEY,
    owner_hasn_id varchar(64) NOT NULL,
    venue varchar(40) NOT NULL,
    label varchar(80) NOT NULL DEFAULT '',
    api_key_encrypted text NOT NULL,
    api_secret_encrypted text NOT NULL,
    passphrase_encrypted text,
    environment varchar(8) NOT NULL DEFAULT 'paper' CHECK (environment IN ('paper', 'live')),
    status varchar(8) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    created_time timestamptz NOT NULL DEFAULT now(),
    updated_time timestamptz DEFAULT now(),
    CONSTRAINT uq_quant_credential UNIQUE (owner_hasn_id, venue, label)
);
COMMENT ON TABLE quant_venue_credential IS 'venue 凭据托管（BYO，key_encryption 加密；owner 经 webui 增删，分身无写工具/不可读明文；明文永不出库面）';
COMMENT ON COLUMN quant_venue_credential.owner_hasn_id IS '归属主人 hasn_id（行级隔离）';
COMMENT ON COLUMN quant_venue_credential.venue IS '场所 (BINANCE/OKX/IB/…)';
COMMENT ON COLUMN quant_venue_credential.api_key_encrypted IS 'API key 密文（common/security key_encryption）';
COMMENT ON COLUMN quant_venue_credential.api_secret_encrypted IS 'API secret 密文';
COMMENT ON COLUMN quant_venue_credential.passphrase_encrypted IS 'passphrase 密文（OKX/Coinbase 等需要；可空）';
COMMENT ON COLUMN quant_venue_credential.environment IS '环境 (paper:模拟盘:blue/live:实盘:red)';
COMMENT ON COLUMN quant_venue_credential.status IS '状态 (active:有效:green/revoked:已撤销:gray)';
CREATE INDEX IF NOT EXISTS idx_quant_credential_owner ON quant_venue_credential (owner_hasn_id);
