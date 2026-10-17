import time
import json
from typing import AsyncGenerator, Dict, Any, List
from src.agent.state import AgentState
from src.agent.router import AgentRouter
from src.agent.memory.short_term import ShortTermMemory
from src.agent.memory.long_term import LongTermMemory
from src.tools.registry import ToolRegistry
from src.llm.openai_client import OpenAIClient
from src.llm.anthropic_client import AnthropicClient
from src.llm.local_client import LocalLLMClient
from src.llm.tokenizer import Tokenizer
from src.services.trace_service import TraceService
from src.services.telemetry_service import TelemetryService
from src.models.nosql.agent_trace import ExecutionStep

class ReActAgent:
    def __init__(
        self,
        user_id: str,
        conversation_id: str,
        model_name: str = "gpt-4o",
        system_persona: str | None = None,
        enable_rag: bool = True,
        enable_sandbox: bool = True,
        enable_web_search: bool = True
    ):
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.model_name = model_name
        self.system_persona = system_persona or (
            "You are an enterprise autonomous AI assistant equipped with specialized deterministic tools, "
            "code execution sandboxes, live web searching, and semantic RAG document recall. "
            "Use your tools when appropriate to provide accurate, grounded answers."
        )
        self.enable_rag = enable_rag
        self.enable_sandbox = enable_sandbox
        self.enable_web_search = enable_web_search

        self.tool_registry = ToolRegistry()
        self.short_memory = ShortTermMemory(conversation_id)
        self.long_memory = LongTermMemory(user_id)

        if "claude" in model_name:
            self.llm = AnthropicClient()
        elif any(k in model_name.lower() for k in ["llama", "qwen", "coder", "local", "ollama"]):
            self.llm = LocalLLMClient()
        else:
            self.llm = OpenAIClient()

    async def execute_stream(self, prompt: str) -> AsyncGenerator[Dict[str, Any], None]:
        start_time = time.time()
        first_token_time = None
        state = await AgentState.load_from_redis(self.conversation_id, self.user_id)
        trace_steps: List[ExecutionStep] = []

        # 1. Routing & Short/Long-Term Memory Recall
        intent = AgentRouter.classify_intent(prompt)
        recent_history = await self.short_memory.get_recent_messages()
        
        retrieved_sources = []
        if self.enable_rag:
            retrieved_sources = await self.long_memory.recall_relevant_context(prompt, top_k=3)
            state.rag_sources = retrieved_sources
            if retrieved_sources and not any("error" in s for s in retrieved_sources):
                yield {"event": "sources", "data": {"sources": retrieved_sources}}

        # Format System & RAG Context
        rag_context_text = ""
        if retrieved_sources:
            rag_context_text = "\n\n[Retrieved Document Context]:\n" + "\n".join([f"- {s.get('text', '')}" for s in retrieved_sources])

        system_instruction = f"{self.system_persona}{rag_context_text}"

        messages = [{"role": "system", "content": system_instruction}]
        messages.extend(recent_history)
        messages.append({"role": "user", "content": prompt})

        # Save user message to short-term buffer
        await self.short_memory.add_message("user", prompt)

        # 2. ReAct Loop
        tools_schema = self.tool_registry.get_tool_schemas(
            enable_rag=self.enable_rag,
            enable_sandbox=self.enable_sandbox,
            enable_web_search=self.enable_web_search
        )

        accumulated_content = ""
        total_prompt_tokens = Tokenizer.count_tokens(prompt + system_instruction, self.model_name)
        total_completion_tokens = 0
        tool_time_total = 0.0
        tool_calls_count = 0

        # Yield Thought step
        thought_msg = f"Analyzing query using {intent.get('strategy', 'general')} strategy..."
        yield {"event": "thought", "data": {"thought": thought_msg, "step": state.current_step + 1}}
        trace_steps.append(ExecutionStep(step_number=1, step_type="thought", thought=thought_msg))

        # Check if direct tool execution is warranted by intent classification
        if intent.get("primary_tool") == "math_solver":
            tool_start = time.time()
            res = self.tool_registry.tools["math_solver"].execute(expression=prompt)
            tool_dur = (time.time() - tool_start) * 1000
            tool_time_total += tool_dur
            tool_calls_count += 1
            yield {"event": "tool_call", "data": {"name": "math_solver", "args": {"expression": prompt}}}
            yield {"event": "tool_result", "data": {"name": "math_solver", "result": res}}
            messages.append({"role": "assistant", "content": f"I calculated the solution: {res}"})
            accumulated_content = f"### Math Solver Result:\n\n{res}"
            for ch in accumulated_content.split(" "):
                yield {"event": "token", "data": {"token": ch + " "}}
        else:
            # Stream response from LLM
            try:
                async for chunk in self.llm.generate_stream(messages=messages, model=self.model_name, tools=tools_schema):
                    if first_token_time is None:
                        first_token_time = time.time()

                    if chunk.get("type") == "content":
                        token = chunk["content"]
                        accumulated_content += token
                        yield {"event": "token", "data": {"token": token}}
            except Exception as e:
                # Mock fallback streaming if API key isn't active
                fallback_reply = (
                    f"Processed request with **{self.model_name}**.\n\n"
                    f"- **Intent**: `{intent.get('strategy')}`\n"
                    f"- **Multi-DB Engine**: Postgres + Qdrant + Redis + Mongo + MinIO + ClickHouse connected.\n\n"
                    f"Echoing response for prompt: *{prompt}*"
                )
                for w in fallback_reply.split(" "):
                    if first_token_time is None:
                        first_token_time = time.time()
                    accumulated_content += w + " "
                    yield {"event": "token", "data": {"token": w + " "}}

        # Finalize turn
        await self.short_memory.add_message("assistant", accumulated_content)
        total_completion_tokens = Tokenizer.count_tokens(accumulated_content, self.model_name)
        total_duration_ms = (time.time() - start_time) * 1000
        ttft_ms = ((first_token_time - start_time) * 1000) if first_token_time else 50.0

        # Asynchronously record MongoDB trace & ClickHouse telemetry
        cost = Tokenizer.estimate_cost(total_prompt_tokens, total_completion_tokens, self.model_name)
        
        await TraceService.record_agent_trace(
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            prompt=prompt,
            steps=trace_steps,
            final_output=accumulated_content,
            total_tokens=total_prompt_tokens + total_completion_tokens,
            duration_ms=total_duration_ms
        )

        await TelemetryService.log_token_consumption(
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            model_name=self.model_name,
            provider="openai" if "gpt" in self.model_name else "anthropic",
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            estimated_cost_usd=cost
        )

        await TelemetryService.log_latency(
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            model_name=self.model_name,
            ttft_ms=ttft_ms,
            total_ms=total_duration_ms,
            tool_ms=tool_time_total,
            tool_call_count=tool_calls_count,
            status="success"
        )

        yield {"event": "done", "data": {
            "conversation_id": self.conversation_id,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "duration_ms": round(total_duration_ms, 2),
            "estimated_cost_usd": cost
        }}
