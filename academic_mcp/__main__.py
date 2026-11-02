"""`python -m academic_mcp`: the read-only MCP server Claude Desktop launches (docs/43)."""
import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main())
