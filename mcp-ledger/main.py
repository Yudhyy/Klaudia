import argparse
import os
from ipaddress import ip_address
from urllib.parse import unquote

from dotenv import load_dotenv

load_dotenv()

import fastmcp  # noqa: E402
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
        transport: stdio or http (Streamable HTTP).

    Raises:
        RuntimeError: A network transport has no auth or explicit dev override.
        ValueError: The transport is removed or unsupported.
    """
    if transport == "sse":
        raise ValueError(
            "SSE transport was removed; supported transports are stdio and http. "
            "Use --transport http with a Streamable HTTP endpoint such as /mcp."
        )
    if transport not in {"stdio", "http"}:
        raise ValueError("Transport must be stdio or http (Streamable HTTP)")
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    if (
        unquote(fastmcp.settings.streamable_http_path)
        .rstrip("/")
        .lower()
        .endswith("/sse")
    ):
        raise ValueError(
            "Legacy /sse endpoints were removed; configure FASTMCP_STREAMABLE_HTTP_PATH "
            "with a Streamable HTTP path such as /mcp."
        )

    host = os.getenv("FASTMCP_HOST", "0.0.0.0")
    port = int(os.getenv("FASTMCP_PORT", "8003"))
    if not os.getenv("MCP_JWT_SECRET"):
        if not _enabled("MCP_ALLOW_INSECURE_HTTP"):
            raise RuntimeError(
                "Network transport requires MCP_JWT_SECRET; set "
                "MCP_ALLOW_INSECURE_HTTP=true only for local development"
            )
        if not _is_loopback(host):
            raise RuntimeError(
                "MCP_ALLOW_INSECURE_HTTP requires a loopback FASTMCP_HOST "
                "such as 127.0.0.1; external binds require MCP_JWT_SECRET"
            )

    mcp.run(
        transport="http",
        host=host,
        port=port,
        host_origin_protection=True,
        stateless_http=True,
    )


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP-Ledger Server")
    parser.add_argument(
        "--transport",
        metavar="{stdio,http}",
        default="stdio",
        help="stdio (default) or Streamable HTTP; legacy SSE has been removed",
    )
    args = parser.parse_args()
    try:
        run_server(args.transport)
    except ValueError as exc:
        parser.error(str(exc))
