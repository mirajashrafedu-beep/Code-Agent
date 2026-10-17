import tiktoken

PRICING_PER_1K_TOKENS = {
    "gpt-4o": {"prompt": 0.005, "completion": 0.015},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "claude-3-5-sonnet-20241022": {"prompt": 0.003, "completion": 0.015},
    "meta-llama/Llama-3.1-8B-Instruct": {"prompt": 0.0001, "completion": 0.0001},
}

class Tokenizer:
    @staticmethod
    def count_tokens(text: str, model_name: str = "gpt-4o") -> int:
        try:
            encoding = tiktoken.encoding_for_model(model_name)
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text or ""))

    @staticmethod
    def estimate_cost(prompt_tokens: int, completion_tokens: int, model_name: str = "gpt-4o") -> float:
        rates = PRICING_PER_1K_TOKENS.get(model_name, {"prompt": 0.002, "completion": 0.006})
        cost = (prompt_tokens / 1000.0) * rates["prompt"] + (completion_tokens / 1000.0) * rates["completion"]
        return round(cost, 6)
