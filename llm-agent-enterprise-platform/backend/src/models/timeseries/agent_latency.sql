-- ClickHouse DDL for Agent Latency & TTFT Telemetry
CREATE TABLE IF NOT EXISTS telemetry_db.agent_latency (
    trace_id UUID DEFAULT generateUUIDv4(),
    user_id UUID,
    conversation_id UUID,
    model_name LowCardinality(String),
    time_to_first_token_ms Float64,
    total_execution_time_ms Float64,
    tool_execution_time_ms Float64,
    tool_call_count UInt16,
    status LowCardinality(String),
    created_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (created_at, user_id, conversation_id);
