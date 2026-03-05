"""
FileTools — file operations available to worker agents.
Registered as Claude API tools for the tool-use loop.
"""

import os
from pathlib import Path


# ── Tool definitions for Claude API ──

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read contents of a file. Returns file text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates directories if needed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and subdirectories in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the directory to list",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list recursively",
                    "default": False,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "create_directory",
        "description": "Create a directory (and parent directories if needed).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of directory to create",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "file_exists",
        "description": "Check if a file or directory exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to check",
                }
            },
            "required": ["path"],
        },
    },
]


# ── Tool implementations ──

def read_file(path: str) -> str:
    """Read file contents."""
    p = Path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(path: str, content: str) -> str:
    """Write content to file, creating directories if needed."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"OK: Written {len(content)} chars to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


def list_directory(path: str, recursive: bool = False) -> str:
    """List directory contents."""
    p = Path(path)
    if not p.exists():
        return f"Error: Directory not found: {path}"
    if not p.is_dir():
        return f"Error: Not a directory: {path}"
    try:
        if recursive:
            items = [str(f.relative_to(p)) for f in p.rglob("*")]
        else:
            items = [f.name for f in p.iterdir()]
        return "\n".join(sorted(items)) if items else "(empty)"
    except Exception as e:
        return f"Error listing directory: {e}"


def create_directory(path: str) -> str:
    """Create directory."""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return f"OK: Directory created: {path}"
    except Exception as e:
        return f"Error creating directory: {e}"


def file_exists(path: str) -> str:
    """Check if path exists."""
    p = Path(path)
    if p.exists():
        kind = "directory" if p.is_dir() else "file"
        return f"Yes: {kind} exists at {path}"
    return f"No: nothing at {path}"


# ── Router for tool-use loop ──

TOOL_EXECUTORS = {
    "read_file": lambda inputs: read_file(inputs["path"]),
    "write_file": lambda inputs: write_file(inputs["path"], inputs["content"]),
    "list_directory": lambda inputs: list_directory(inputs["path"], inputs.get("recursive", False)),
    "create_directory": lambda inputs: create_directory(inputs["path"]),
    "file_exists": lambda inputs: file_exists(inputs["path"]),
}


def execute(name: str, inputs: dict) -> str:
    """Route tool call to implementation."""
    executor = TOOL_EXECUTORS.get(name)
    if executor:
        return executor(inputs)
    return f"Unknown file tool: {name}"
