"""TigerGraph connection test script.

Loads credentials from a `.env` file, creates a pyTigerGraph connection,
pings the host, fetches an auth token, checks connection health, and prints
cluster status information. Any runtime connection errors are caught and
reported gracefully so the script can be validated for syntax and packaging
independently of live credentials.
"""

import os
import sys
from typing import List

from dotenv import load_dotenv
from pyTigerGraph import TigerGraphConnection


def get_required_env(name: str) -> str:
    """Return a stripped environment variable or an empty string."""
    return os.getenv(name, "").strip()


def validate_env() -> List[str]:
    """Return a list of required variables that are missing or still placeholders."""
    required = ["TG_HOST", "TG_USERNAME", "TG_PASSWORD", "TG_SECRET"]
    missing = []
    for var in required:
        value = get_required_env(var)
        if not value or "your-" in value.lower():
            missing.append(var)
    return missing


def main() -> int:
    # Load environment variables from .env in the project root.
    load_dotenv()

    missing = validate_env()
    if missing:
        print(
            "Missing or placeholder environment variables: "
            f"{', '.join(missing)}",
            file=sys.stderr,
        )
        print("Update the .env file with valid credentials and rerun.", file=sys.stderr)
        return 1

    host = get_required_env("TG_HOST")
    username = get_required_env("TG_USERNAME")
    password = get_required_env("TG_PASSWORD")
    graph = get_required_env("TG_GRAPH") or "FraudGraph"
    secret = get_required_env("TG_SECRET")

    print(f"Connecting to TigerGraph at {host} (graph: {graph}) as {username}...")

    conn = TigerGraphConnection(
        host=host,
        graphname=graph,
        username=username,
        password=password,
    )

    # 1. Ping the database host.
    print("\n1. Pinging database host...")
    try:
        echo_response = conn.echo()
        print(f"   Echo response: {echo_response}")
    except Exception as exc:  # noqa: BLE001
        print(f"   Ping failed: {exc}", file=sys.stderr)

    # 2. Retrieve an auth token.
    print("\n2. Retrieving auth token via getToken()...")
    try:
        token = conn.getToken(secret)
        # getToken may return a string or tuple/list; display safely.
        if isinstance(token, (list, tuple)):
            display_token = token[0] if token else ""
        else:
            display_token = token
        if isinstance(display_token, str) and len(display_token) > 32:
            display_token = display_token[:32] + "..."
        print(f"   Token retrieved: {display_token}")
    except Exception as exc:  # noqa: BLE001
        print(f"   Token retrieval failed: {exc}", file=sys.stderr)

    # 3. Verify connection health (TigerGraph version).
    print("\n3. Verifying connection health...")
    try:
        version = conn.getVersion()
        print(f"   TigerGraph version: {version}")
    except Exception as exc:  # noqa: BLE001
        print(f"   Health check failed: {exc}", file=sys.stderr)

    # 4. Print cluster status.
    print("\n4. Fetching cluster status...")
    try:
        status = conn.gsql("SHOW STATUS", options=[])
        print(f"   Cluster status:\n{status}")
    except Exception as exc:  # noqa: BLE001
        print(f"   Cluster status lookup failed: {exc}", file=sys.stderr)

    print("\nConnection test complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
