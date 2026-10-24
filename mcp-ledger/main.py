import argparse
import os

from dotenv import load_dotenv

load_dotenv()

from ledger.server import mcp  # noqa: E402


def _enabled(name: str) -> bool:
    """Return whether an environment flag is enabled.

    Args:
        name: Environment variable name.

    Returns:
        True for 1, true, yes, or on, ignoring case.
    """
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def run_server(transport: str) -> None:
    """Run the ledger server on the selected transport.

    Args:
        transport: One of stdio, http, or legacy sse.

    Raises:
        RuntimeError: A network transport has no auth or explicit dev override.
    """
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    host = os.getenv("FASTMCP_HOST", "0.0.0.0")
    port = int(os.getenv("FASTMCP_PORT", "8003"))
    if (
        transport in {"http", "sse"}
        and not os.getenv("MCP_JWT_SECRET")
        and not _enabled("MCP_ALLOW_INSECURE_HTTP")
    ):
        raise RuntimeError(
            "Network transport requires MCP_JWT_SECRET; set "
            "MCP_ALLOW_INSECURE_HTTP=true only for local development"
        )

    kwargs = {
        "host": host,
        "port": port,
        "host_origin_protection": "auto",
    }
    if transport == "http":
        kwargs["stateless_http"] = True
    mcp.run(transport=transport, **kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP-Ledger Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default="stdio",
        help="Transport mode; sse is legacy (default: stdio)",
    )
    args = parser.parse_args()
    run_server(args.transport)
