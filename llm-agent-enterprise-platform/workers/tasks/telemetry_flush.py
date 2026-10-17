import os
import json
from celery import shared_task
import redis
import clickhouse_connect

REDIS_URI = os.getenv("REDIS_URI", "redis://redis:6379/0")
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "clickhouse_secure_password")
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "telemetry_db")

@shared_task(name="tasks.flush_telemetry_batch")
def flush_telemetry_batch():
    r = redis.from_url(REDIS_URI)
    
    # 1. Pop token consumption batch
    token_entries = []
    while True:
        item = r.lpop("telemetry:token_queue")
        if not item:
            break
        token_entries.append(json.loads(item))

    # 2. Pop latency batch
    latency_entries = []
    while True:
        item = r.lpop("telemetry:latency_queue")
        if not item:
            break
        latency_entries.append(json.loads(item))

    if not token_entries and not latency_entries:
        return {"status": "skipped", "flushed": 0}

    try:
        ch_client = clickhouse_connect.get_client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            username=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DB
        )

        if token_entries:
            rows = [
                [
                    entry.get("user_id"),
                    entry.get("conversation_id"),
                    entry.get("model_name"),
                    entry.get("provider", "openai"),
                    entry.get("prompt_tokens", 0),
                    entry.get("completion_tokens", 0),
                    entry.get("total_tokens", 0),
                    entry.get("estimated_cost_usd", 0.0)
                ]
                for entry in token_entries
            ]
            ch_client.insert(
                "token_consumption",
                rows,
                column_names=[
                    "user_id", "conversation_id", "model_name", "provider",
                    "prompt_tokens", "completion_tokens", "total_tokens", "estimated_cost_usd"
                ]
            )

        if latency_entries:
            rows = [
                [
                    entry.get("user_id"),
                    entry.get("conversation_id"),
                    entry.get("model_name"),
                    entry.get("time_to_first_token_ms", 0.0),
                    entry.get("total_execution_time_ms", 0.0),
                    entry.get("tool_execution_time_ms", 0.0),
                    entry.get("tool_call_count", 0),
                    entry.get("status", "success")
                ]
                for entry in latency_entries
            ]
            ch_client.insert(
                "agent_latency",
                rows,
                column_names=[
                    "user_id", "conversation_id", "model_name",
                    "time_to_first_token_ms", "total_execution_time_ms",
                    "tool_execution_time_ms", "tool_call_count", "status"
                ]
            )

        return {
            "status": "success",
            "flushed_tokens": len(token_entries),
            "flushed_latencies": len(latency_entries)
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}
