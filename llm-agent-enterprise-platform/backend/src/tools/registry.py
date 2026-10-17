from typing import Dict, Any, List
from .math_solver import MathSolverTool
from .web_search import WebSearchTool
from .rag_retriever import RAGRetrieverTool
from .sandbox_runner import SandboxRunnerTool

class ToolRegistry:
    def __init__(self):
        self.tools = {
            MathSolverTool.name: MathSolverTool,
            WebSearchTool.name: WebSearchTool,
            RAGRetrieverTool.name: RAGRetrieverTool,
            SandboxRunnerTool.name: SandboxRunnerTool,
        }

    def get_tool_schemas(
        self,
        enable_rag: bool = True,
        enable_sandbox: bool = True,
        enable_web_search: bool = True
    ) -> List[Dict[str, Any]]:
        schemas = [MathSolverTool.get_schema()]
        if enable_rag:
            schemas.append(RAGRetrieverTool.get_schema())
        if enable_sandbox:
            schemas.append(SandboxRunnerTool.get_schema())
        if enable_web_search:
            schemas.append(WebSearchTool.get_schema())
        return schemas

    async def execute_tool(self, name: str, args: Dict[str, Any], user_id: str = "") -> Any:
        if name == MathSolverTool.name:
            return MathSolverTool.execute(**args)
        elif name == WebSearchTool.name:
            return await WebSearchTool.execute(**args)
        elif name == RAGRetrieverTool.name:
            return await RAGRetrieverTool.execute(user_id=user_id, **args)
        elif name == SandboxRunnerTool.name:
            return await SandboxRunnerTool.execute(**args)
        else:
            return f"Unknown tool: {name}"
