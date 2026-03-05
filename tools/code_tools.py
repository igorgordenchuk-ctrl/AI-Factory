"""
CodeTools — code execution tools available to worker agents.
Provides sandboxed Python execution, test running, and linting.
"""

import subprocess
import sys
import tempfile
from pathlib import Path


TOOL_DEFINITIONS = [
    {
        "name": "run_python",
        "description": "Execute Python code and return stdout/stderr. Use for computations, data processing, file generation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory for execution (optional)",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run pytest in a directory and return results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "test_path": {
                    "type": "string",
                    "description": "Path to test file or directory",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory (optional)",
                },
            },
            "required": ["test_path"],
        },
    },
    {
        "name": "run_command",
        "description": "Execute a shell command and return output. Use for npm, pip, git, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Working directory (optional)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60)",
                    "default": 60,
                },
            },
            "required": ["command"],
        },
    },
]


def run_python(code: str, working_dir: str = "") -> str:
    """Execute Python code in a subprocess."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        cwd = working_dir if working_dir else None
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )

        Path(tmp_path).unlink(missing_ok=True)

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"

        return output.strip() if output.strip() else "(no output)"

    except subprocess.TimeoutExpired:
        return "Error: Execution timed out (120s)"
    except Exception as e:
        return f"Error: {e}"


def run_tests(test_path: str, working_dir: str = "") -> str:
    """Run pytest and return results."""
    try:
        cwd = working_dir if working_dir else None
        result = subprocess.run(
            [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=cwd,
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        return output.strip() if output.strip() else "(no output)"

    except subprocess.TimeoutExpired:
        return "Error: Tests timed out (300s)"
    except Exception as e:
        return f"Error running tests: {e}"


def run_command(command: str, working_dir: str = "", timeout: int = 60) -> str:
    """Execute shell command."""
    # Базовая защита от опасных команд
    dangerous = ["rm -rf /", "format ", "del /f /s /q C:\\"]
    for d in dangerous:
        if d in command.lower():
            return f"Error: Blocked dangerous command: {command}"

    try:
        cwd = working_dir if working_dir else None
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"

        return output.strip() if output.strip() else "(no output)"

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out ({timeout}s)"
    except Exception as e:
        return f"Error: {e}"


# ── Router ──

TOOL_EXECUTORS = {
    "run_python": lambda inputs: run_python(inputs["code"], inputs.get("working_dir", "")),
    "run_tests": lambda inputs: run_tests(inputs["test_path"], inputs.get("working_dir", "")),
    "run_command": lambda inputs: run_command(
        inputs["command"], inputs.get("working_dir", ""), inputs.get("timeout", 60)
    ),
}


def execute(name: str, inputs: dict) -> str:
    """Route tool call to implementation."""
    executor = TOOL_EXECUTORS.get(name)
    if executor:
        return executor(inputs)
    return f"Unknown code tool: {name}"
