-- ClickHouse DDL for Token Consumption Telemetry
CREATE TABLE IF NOT EXISTS telemetry_db.token_consumption (
    event_id UUID DEFAULT generateUUIDv4(),
    user_id UUID,
    conversation_id UUID,
    model_name LowCardinality(String),
    provider LowCardinality(String),
    prompt_tokens UInt32,
    completion_tokens UInt32,
    total_tokens UInt32,
    estimated_cost_usd Float64,
    created_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (created_at, user_id, conversation_id);
