"""Probe TigerGraph authentication methods without loading environment variables."""

import pyTigerGraph as tg


HOST = "https://tg-c7bcc8d5-78a3-4e5f-b552-5b48958ff01f.tg-2635877100.i.tgcloud.io"
GRAPH = "FraudGraph"
SECRET = "p26jjpu9nbi3kk4bkef8km4f1rdnaffm"
WORKSPACE_SECRET = "k2kngaagulic2cn32s2hcjc20kc5tu04"


def print_exception(prefix, exc):
    print(f"{prefix} exception type: {type(exc).__name__}")
    print(f"{prefix} raw exception: {exc!r}")


def probe_secret_method(label, username):
    print(f"\n=== {label} ===")
    try:
        conn = tg.TigerGraphConnection(
            host=HOST,
            graphname=GRAPH,
            username=username,
            password="",
        )
        result = conn.getToken(secret=SECRET)
        print(f"{label} getToken raw result: {result!r}")
        print(f"{label} auth token set: {bool(conn.apiToken)}")
    except BaseException as exc:  # noqa: BLE001
        print_exception(label, exc)


def probe_workspace_token():
    label = "Method C: Workspace Direct API Token"
    print(f"\n=== {label} ===")
    try:
        conn = tg.TigerGraphConnection(
            host=HOST,
            graphname=GRAPH,
            username="",
            password="",
            apiToken=WORKSPACE_SECRET,
        )
        result = conn.echo()
        print(f"{label} echo raw result: {result!r}")
    except BaseException as exc:  # noqa: BLE001
        print_exception(label, exc)


def main():
    print("TigerGraph authentication diagnostics")
    print(f"Host: {HOST}")
    print(f"Graph: {GRAPH}")
    probe_secret_method("Method A: Standard GSQL Secret Token Request", "tigergraph")
    probe_secret_method("Method B: Email-bound GSQL Secret Token Request", "sawkare.om@gmail.com")
    probe_workspace_token()


if __name__ == "__main__":
    main()
