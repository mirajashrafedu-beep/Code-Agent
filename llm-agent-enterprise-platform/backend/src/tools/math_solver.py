import sympy
from typing import Dict, Any

class MathSolverTool:
    name = "math_solver"
    description = "Deterministic mathematical and symbolic equation solver using SymPy."

    @staticmethod
    def get_schema() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "math_solver",
                "description": "Evaluates expressions, solves algebraic equations, computes derivatives and integrals with SymPy.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "The mathematical expression or equation to solve, e.g. 'integrate(x**2 + sin(x), x)' or 'solve(x**2 - 4, x)'"
                        }
                    },
                    "required": ["expression"]
                }
            }
        }

    @staticmethod
    def execute(expression: str) -> str:
        try:
            # Parse safe expression
            result = sympy.sympify(expression)
            return f"Exact Result: {result} | Numeric: {result.evalf() if hasattr(result, 'evalf') else result}"
        except Exception as e:
            return f"Math solver error: {str(e)}"
