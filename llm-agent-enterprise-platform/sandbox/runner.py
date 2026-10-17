import sys
import io
import traceback
from contextlib import redirect_stdout, redirect_stderr
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Isolated Code Interpreter Sandbox")

class CodeExecutionRequest(BaseModel):
    code: str
    timeout_seconds: int = 15

class CodeExecutionResponse(BaseModel):
    stdout: str
    stderr: str
    success: bool
    result: str | None = None

@app.get("/health")
def health():
    return {"status": "healthy", "service": "sandbox"}

@app.post("/execute", response_model=CodeExecutionResponse)
def execute_code(req: CodeExecutionRequest):
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    
    # Safe global execution namespace
    safe_globals = {
        "__builtins__": __builtins__,
        "sys": sys,
    }
    
    # Pre-import common scientific libraries for convenience
    for module_name in ["math", "random", "datetime", "json", "re", "collections", "itertools"]:
        try:
            safe_globals[module_name] = __import__(module_name)
        except ImportError:
            pass

    for opt_mod in ["numpy", "pandas", "scipy", "sympy", "matplotlib"]:
        try:
            safe_globals[opt_mod] = __import__(opt_mod)
        except ImportError:
            pass

    local_vars = {}
    success = True
    result_val = None

    try:
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            # Try to compile and execute
            compiled = compile(req.code, "<sandbox>", "exec")
            exec(compiled, safe_globals, local_vars)
            
            # Extract returned variable if any named 'result'
            if "result" in local_vars:
                result_val = str(local_vars["result"])
    except Exception:
        success = False
        stderr_capture.write(traceback.format_exc())

    return CodeExecutionResponse(
        stdout=stdout_capture.getvalue(),
        stderr=stderr_capture.getvalue(),
        success=success,
        result=result_val
    )
