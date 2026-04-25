#!/usr/bin/env python3
"""
Entry point de despliegue para el servidor MCP.

Prefect Horizon puede apuntar a este archivo con `server.py:mcp`.
"""

from pathlib import Path
import sys


root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir / "src"))

from hey_i_mcp.server import mcp  # noqa: E402


__all__ = ["mcp"]


if __name__ == "__main__":
    mcp.run()