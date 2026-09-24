"""Install and smoke-test the fraud investigation queries."""

import os
import sys

import pyTigerGraph as tg
from dotenv import find_dotenv, load_dotenv


load_dotenv(find_dotenv(), override=True)

GSQL_QUERY = '''USE GRAPH FraudGraph

CREATE OR REPLACE QUERY get_card_history(STRING in_card_id, INT limit_txns = 50) FOR GRAPH FraudGraph {
    TYPEDEF TUPLE <
        STRING txn_id,
        DOUBLE amt,
        DATETIME ts,
        STRING channel,
        DOUBLE risk_score,
        STRING product_cd,
        STRING addr1
    > TxnRecord;

    HeapAccum<TxnRecord>(limit_txns, ts DESC) @@recent_txns;

    StartCard = { to_vertex(in_card_id, "Card") };

    Txns = SELECT t FROM StartCard:s -(MADE:e)-> Transaction:t
           ACCUM @@recent_txns += TxnRecord(
               t.txn_id, 
               t.amt, 
               t.ts, 
               t.channel, 
               t.risk_score, 
               t.product_cd, 
               t.addr1
           );

    PRINT @@recent_txns AS history;
}

INSTALL QUERY get_card_history'''


def select_smoke_card(conn):
    configured_card = os.environ.get("SMOKE_CARD_ID", "").strip()
    if configured_card:
        return configured_card

    vertices = conn.getVertices("Card", limit=1)
    if isinstance(vertices, list) and vertices:
        vertex = vertices[0]
        if isinstance(vertex, dict):
            return vertex.get("v_id") or vertex.get("id")
    if isinstance(vertices, dict):
        return vertices.get("v_id") or vertices.get("id")
    return None


def main() -> int:
    conn = tg.TigerGraphConnection(
        host=os.environ.get("TG_HOST"),
        graphname=os.environ.get("TG_GRAPH"),
        username=os.environ.get("TG_USERNAME"),
        password=os.environ.get("TG_PASSWORD"),
    )

    print("Generating bearer token...")
    conn.getToken()

    print("Registering and installing get_card_history...")
    install_result = conn.gsql(GSQL_QUERY)
    print("Install response:")
    print(install_result)

    card_id = select_smoke_card(conn)
    if not card_id:
        print("Unable to select a Card vertex for the smoke test.", file=sys.stderr)
        return 1

    print(f"Smoke-testing get_card_history with card_id={card_id}...")
    result = conn.runInstalledQuery(
        "get_card_history",
        params={"in_card_id": card_id, "limit_txns": 50},
    )
    print("Smoke-test response:")
    print(result)
    print("get_card_history installed and smoke-tested successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
