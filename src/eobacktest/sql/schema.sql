CREATE OR REPLACE TABLE phone_model (
    model_id VARCHAR PRIMARY KEY,
    launch_month DATE NOT NULL,
    discontinued_month DATE
);
CREATE OR REPLACE TABLE sku (
    sku_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    phone_model VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    unit_cost DOUBLE NOT NULL
);
CREATE OR REPLACE TABLE lifecycle (
    sku_id VARCHAR NOT NULL,
    from_month DATE NOT NULL,
    stage VARCHAR NOT NULL
);
CREATE OR REPLACE TABLE inventory_snapshot (
    snapshot_date DATE NOT NULL,
    sku_id VARCHAR NOT NULL,
    on_hand_units BIGINT NOT NULL,
    unit_cost DOUBLE NOT NULL,
    last_movement_date DATE NOT NULL
);
CREATE OR REPLACE TABLE forecast_version (
    version_date DATE NOT NULL,
    sku_id VARCHAR NOT NULL,
    target_month DATE NOT NULL,
    forecast_units DOUBLE NOT NULL
);
CREATE OR REPLACE TABLE sales (
    sales_month DATE NOT NULL,
    sku_id VARCHAR NOT NULL,
    units_sold BIGINT NOT NULL
);
CREATE OR REPLACE TABLE scrap (
    txn_date DATE NOT NULL,
    sku_id VARCHAR NOT NULL,
    units BIGINT NOT NULL,
    unit_cost DOUBLE NOT NULL
);
CREATE OR REPLACE TABLE position_asof (
    snapshot_date DATE NOT NULL,
    sku_id VARCHAR NOT NULL,
    on_hand_units BIGINT NOT NULL,
    unit_cost DOUBLE NOT NULL,
    last_movement_date DATE NOT NULL,
    days_since_movement INTEGER NOT NULL,
    forecast_version_date DATE,
    forecast_12m_units DOUBLE NOT NULL,
    trailing_avg_usage DOUBLE NOT NULL
);
CREATE OR REPLACE TABLE reserve_asof (
    snapshot_date DATE NOT NULL,
    sku_id VARCHAR NOT NULL,
    policy VARCHAR NOT NULL,
    reserve_units DOUBLE NOT NULL,
    reserve_usd DOUBLE NOT NULL
);
CREATE OR REPLACE TABLE realized_asof (
    snapshot_date DATE NOT NULL,
    sku_id VARCHAR NOT NULL,
    on_hand_units BIGINT NOT NULL,
    unit_cost DOUBLE NOT NULL,
    units_sold_next_12m BIGINT NOT NULL,
    realized_units BIGINT NOT NULL,
    realized_usd DOUBLE NOT NULL
)
