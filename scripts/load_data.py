import os
import argparse
import ast
import hashlib
import time
from pathlib import Path

import pyTigerGraph as tg
from dotenv import find_dotenv, load_dotenv
import pandas as pd

# Explicitly find and load the project .env file.
load_dotenv(find_dotenv(), override=True)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
CHUNK_SIZE = 5000
SAMPLE_ROWS = 1000

TXN_USECOLS = {
    "TransactionID", "TransactionAmt", "ts", "channel", "risk_score",
    "ProductCD", "addr1", "addr2", "customer_id", "card1", "card4",
    "card5", "card6", "card_id",
}
IDENTITY_USECOLS = {
    "TransactionID", "DeviceInfo", "DeviceType", "id_30", "id_31", "id_33",
}
CASE_USECOLS = {
    "case_id", "customer_id", "card_id", "outcome", "pattern",
    "exposure_usd", "report_filed", "analyst_notes", "txn_ids",
}


def safe_str(value):
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none", "nat"):
        return ""
    return text


def safe_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def safe_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    return safe_str(value).lower() in ("true", "t", "yes", "y", "1")


def numeric_str(value):
    """Render numeric codes like 299.0 as '299'; pass other values through."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return safe_str(value)
    if pd.isna(number):
        return ""
    return str(int(number)) if number == int(number) else str(number)


def parse_id_list(raw):
    """Parse a pipe-separated (or python-literal list) column of ids."""
    text = safe_str(raw)
    if not text:
        return []
    if text.startswith("["):
        try:
            value = ast.literal_eval(text)
            if isinstance(value, (list, tuple, set)):
                return [safe_str(item) for item in value if safe_str(item)]
        except (ValueError, SyntaxError):
            pass
    return [part.strip() for part in text.split("|") if part.strip()]


def chunks(sequence):
    for start in range(0, len(sequence), CHUNK_SIZE):
        yield sequence[start:start + CHUNK_SIZE]


def summarize(result):
    if isinstance(result, dict):
        return result
    return {"submitted": result}


def upsert_vertices(conn, vertex_type, records):
    records = list(records)
    if not records:
        print(f"  {vertex_type}: nothing to upsert")
        return 0
    batches = list(chunks(records))
    print(f"  {vertex_type}: upserting {len(records):,} vertices "
          f"in {len(batches)} batch(es) of <= {CHUNK_SIZE:,}")
    accepted = 0
    for index, chunk in enumerate(batches, start=1):
        result = conn.upsertVertices(vertex_type, chunk)
        accepted += len(chunk)
        if index % 10 == 0 or index == len(batches):
            print(f"    batch {index}/{len(batches)} -> {summarize(result)}")
    return accepted


def upsert_edges(conn, source_type, edge_type, target_type, edges):
    edges = list(edges)
    if not edges:
        print(f"  {edge_type}: nothing to upsert")
        return 0
    batches = list(chunks(edges))
    print(f"  {edge_type} ({source_type} -> {target_type}): upserting "
          f"{len(edges):,} edges in {len(batches)} batch(es)")
    submitted = 0
    for index, chunk in enumerate(batches, start=1):
        result = conn.upsertEdges(
            source_type, edge_type, target_type, chunk, vertexMustExist=True
        )
        submitted += len(chunk)
        if index % 10 == 0 or index == len(batches):
            print(f"    batch {index}/{len(batches)} -> {summarize(result)}")
    return submitted


def derive_card_ids(df):
    """card_id = '<customer_id>-K<n>' where n is the first-seen rank of the
    distinct (card1, card5) issuer tuple within each customer. Verified against
    closed_cases_history.csv card_ids (1907/1913 exact matches)."""
    if "card_id" in df.columns and df["card_id"].notna().any():
        return df["card_id"].map(safe_str)
    key = pd.DataFrame({
        "customer_id": df["customer_id"].map(safe_str),
        "k1": df["card1"].map(lambda v: -1.0 if pd.isna(v) else float(v)),
        "k2": df["card5"].map(lambda v: -1.0 if pd.isna(v) else float(v)),
    })
    uniq = key.drop_duplicates()
    uniq = uniq.assign(
        card_index=uniq.groupby("customer_id", sort=False).cumcount() + 1
    )
    merged = key.merge(uniq, on=["customer_id", "k1", "k2"], how="left")
    return merged["customer_id"] + "-K" + merged["card_index"].astype(str)


def init_connection():
    conn = tg.TigerGraphConnection(
        host=os.environ.get("TG_HOST"),
        graphname=os.environ.get("TG_GRAPH"),
        username=os.environ.get("TG_USERNAME"),
        password=os.environ.get("TG_PASSWORD"),
    )
    conn.getToken()
    return conn


def load_transactions(conn, nrows):
    path = DATA_DIR / "transactions.csv"
    print(f"\n=== transactions.csv (nrows={nrows}) ===")
    started = time.time()
    df = pd.read_csv(
        path,
        nrows=nrows,
        usecols=lambda c: c in TXN_USECOLS,
        dtype={"TransactionID": str, "customer_id": str},
    )
    df = df[df["TransactionID"].notna()].copy()
    print(f"  read {len(df):,} rows in {time.time() - started:.1f}s "
          f"(columns: {', '.join(df.columns)})")

    df["card_id"] = derive_card_ids(df)

    transactions = {}
    customers = {}
    cards = {}
    regions = {}
    owns_pairs = set()
    made_edges = []
    billed_edges = []

    for row in df.itertuples(index=False):
        txn_id = safe_str(row.TransactionID)
        if not txn_id:
            continue
        transactions[txn_id] = [txn_id, {
            "amt": safe_float(row.TransactionAmt),
            "ts": safe_str(getattr(row, "ts", None)),
            "channel": safe_str(getattr(row, "channel", None)),
            "risk_score": safe_float(getattr(row, "risk_score", None)),
            "product_cd": safe_str(getattr(row, "ProductCD", None)),
            "addr1": numeric_str(getattr(row, "addr1", None)),
            "addr2": numeric_str(getattr(row, "addr2", None)),
        }]

        customer_id = safe_str(row.customer_id)
        card_id = safe_str(row.card_id)
        if customer_id:
            customers.setdefault(customer_id, [customer_id, {}])
        if card_id:
            card = cards.setdefault(card_id, [card_id, {}])
            network = safe_str(getattr(row, "card4", None))
            card_type = safe_str(getattr(row, "card6", None))
            if network and not card[1].get("card_network"):
                card[1]["card_network"] = network
            if card_type and not card[1].get("card_type"):
                card[1]["card_type"] = card_type
        if customer_id and card_id:
            owns_pairs.add((customer_id, card_id))
            made_edges.append([card_id, txn_id, {}])

        region = numeric_str(getattr(row, "addr1", None))
        if region:
            regions.setdefault(region, [region, {}])
            billed_edges.append([txn_id, region, {}])

    upsert_vertices(conn, "Customer", customers.values())
    upsert_vertices(conn, "Card", cards.values())
    upsert_vertices(conn, "Transaction", transactions.values())
    upsert_vertices(conn, "BillingRegion", regions.values())

    upsert_edges(
        conn, "Customer", "OWNS", "Card",
        [[c, k, {}] for c, k in sorted(owns_pairs)],
    )
    upsert_edges(conn, "Card", "MADE", "Transaction", made_edges)
    upsert_edges(conn, "Transaction", "BILLED_IN", "BillingRegion", billed_edges)

    print(f"  transactions stage done in {time.time() - started:.1f}s")
    return pd.DataFrame({"TransactionID": list(transactions.keys())})


def load_identity(conn, txn_frame, nrows):
    path = DATA_DIR / "identity.csv"
    print(f"\n=== identity.csv (nrows={nrows}) ===")
    started = time.time()
    df = pd.read_csv(
        path,
        nrows=nrows,
        usecols=lambda c: c in IDENTITY_USECOLS,
        dtype={"TransactionID": str},
    )
    print(f"  read {len(df):,} identity rows")

    merged = df.merge(txn_frame, on="TransactionID", how="inner")
    print(f"  merged with transactions on TransactionID: {len(merged):,} rows")

    profiles = {}
    device_edges = []
    for row in merged.itertuples(index=False):
        txn_id = safe_str(row.TransactionID)
        device_info = safe_str(getattr(row, "DeviceInfo", None))
        os_name = safe_str(getattr(row, "id_30", None))
        browser = safe_str(getattr(row, "id_31", None))
        screen = safe_str(getattr(row, "id_33", None))
        device_type = safe_str(getattr(row, "DeviceType", None))
        raw_key = "\x1f".join([device_info, os_name, browser, screen])
        profile_id = "DEV-" + hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:16]
        profiles.setdefault(profile_id, [profile_id, {
            "device_info": device_info,
            "os": os_name,
            "browser": browser,
            "screen": screen,
            "device_type": device_type,
        }])
        device_edges.append([txn_id, profile_id, {}])

    upsert_vertices(conn, "DeviceProfile", profiles.values())
    upsert_edges(
        conn, "Transaction", "FROM_DEVICE", "DeviceProfile", device_edges
    )
    print(f"  identity stage done in {time.time() - started:.1f}s")


def load_closed_cases(conn, nrows):
    path = DATA_DIR / "closed_cases_history.csv"
    print(f"\n=== closed_cases_history.csv (nrows={nrows}) ===")
    started = time.time()
    df = pd.read_csv(path, nrows=nrows, usecols=lambda c: c in CASE_USECOLS)
    print(f"  read {len(df):,} closed cases")

    vertices = []
    on_card_edges = []
    involves_edges = []
    for row in df.itertuples(index=False):
        case_id = safe_str(row.case_id)
        if not case_id:
            continue
        vertices.append([case_id, {
            "customer_id": safe_str(row.customer_id),
            "card_id": safe_str(row.card_id),
            "outcome": safe_str(row.outcome),
            "pattern": safe_str(row.pattern),
            "exposure_usd": safe_float(row.exposure_usd),
            "report_filed": safe_bool(row.report_filed),
            "analyst_notes": safe_str(row.analyst_notes),
        }])
        card_id = safe_str(row.card_id)
        if card_id:
            on_card_edges.append([case_id, card_id, {}])
        for txn_id in parse_id_list(getattr(row, "txn_ids", None)):
            involves_edges.append([case_id, txn_id, {}])

    upsert_vertices(conn, "ClosedCase", vertices)
    upsert_edges(conn, "ClosedCase", "ON_CARD", "Card", on_card_edges)
    upsert_edges(conn, "ClosedCase", "INVOLVES", "Transaction", involves_edges)
    print(f"  closed cases stage done in {time.time() - started:.1f}s")


def main():
    parser = argparse.ArgumentParser(
        description="Ingest the fraud dataset into TigerGraph (FraudGraph)."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--sample", action="store_true",
        help=f"load only the first {SAMPLE_ROWS} rows of each file",
    )
    mode.add_argument(
        "--full", action="store_true", help="load the entire dataset"
    )
    args = parser.parse_args()

    nrows = None if args.full else SAMPLE_ROWS
    if args.full:
        print("Mode: --full (entire dataset)")
    else:
        print(f"Mode: --sample (first {SAMPLE_ROWS} rows of each file)")

    conn = init_connection()
    started = time.time()

    txn_frame = load_transactions(conn, nrows)
    load_identity(conn, txn_frame, nrows)
    load_closed_cases(conn, nrows)

    print(f"\nAll stages completed in {time.time() - started:.1f}s")
    try:
        print("Vertex counts:", conn.getVertexCount())
        print("Edge counts:", conn.getEdgeCount())
    except Exception as exc:  # noqa: BLE001
        print(f"(could not fetch graph counts: {exc})")


if __name__ == "__main__":
    main()
