"""Run the installed MCP host through the retained script path."""

from klaudia_api.interfaces.mcp.host import main, run_server
from klaudia_api.interfaces.mcp.host import mcp as mcp

__all__ = ["main", "run_server", "mcp"]


if __name__ == "__main__":
    main()
