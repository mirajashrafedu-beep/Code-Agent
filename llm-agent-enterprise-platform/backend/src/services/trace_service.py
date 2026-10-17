from typing import Any, List, Optional
from datetime import datetime
from src.models.nosql.agent_trace import AgentTraceDocument, ExecutionStep
from src.models.nosql.tool_logs import ToolLogDocument
from src.utils.logger import logger

class TraceService:
    @staticmethod
    async def record_agent_trace(
        user_id: str,
        conversation_id: str,
        prompt: str,
        steps: List[ExecutionStep],
        final_output: str,
        total_tokens: int,
        duration_ms: float,
        message_id: Optional[str] = None
    ):
        try:
            trace = AgentTraceDocument(
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=message_id,
                prompt=prompt,
                steps=steps,
                final_output=final_output,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                created_at=datetime.utcnow()
            )
            await trace.insert()
        except Exception as e:
            logger.error(f"Failed to record MongoDB agent trace: {e}")

    @staticmethod
    async def record_tool_log(
        user_id: str,
        conversation_id: str,
        tool_name: str,
        input_payload: Any,
        output_payload: Any,
        status: str,
        execution_time_ms: float,
        error_message: Optional[str] = None
    ):
        try:
            log_doc = ToolLogDocument(
                user_id=user_id,
                conversation_id=conversation_id,
                tool_name=tool_name,
                input_payload=input_payload,
                output_payload=output_payload,
                status=status,
                error_message=error_message,
                execution_time_ms=execution_time_ms,
                created_at=datetime.utcnow()
            )
            await log_doc.insert()
        except Exception as e:
            logger.error(f"Failed to record MongoDB tool log: {e}")
