import os
import httpx
from typing import Dict, Any

SANDBOX_URL = os.getenv("SANDBOX_URL", "http://sandbox:8080")

class SandboxRunnerTool:
    name = "sandbox_runner"
    description = "Executes arbitrary Python code in a secure, memory-isolated sandbox."

    @staticmethod
    def get_schema() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "sandbox_runner",
                "description": "Executes Python code in a sandboxed interpreter. Use this for computations, data analysis, or executing algorithms.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Valid Python code to execute"
                        }
                    },
                    "required": ["code"]
                }
            }
        }

    @staticmethod
    async def execute(code: str) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(f"{SANDBOX_URL}/execute", json={"code": code})
                if res.status_code == 200:
                    return res.json()
                return {"success": False, "stderr": f"Sandbox HTTP {res.status_code}: {res.text}", "stdout": ""}
        except Exception as e:
            return {"success": False, "stderr": f"Failed to connect to Sandbox: {str(e)}", "stdout": ""}
