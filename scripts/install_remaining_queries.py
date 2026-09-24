"""Install the remaining LangGraph agent queries in one GSQL request."""

import os
import sys

import pyTigerGraph as tg
from dotenv import find_dotenv, load_dotenv


load_dotenv(find_dotenv(), override=True)

GSQL_BLOCK = '''USE GRAPH FraudGraph

// 1. Device Neighbors Fix (Forward scan to bypass missing reverse edges)
CREATE OR REPLACE QUERY get_device_neighbors(VERTEX<DeviceProfile> in_device_node) FOR GRAPH FraudGraph {
    TYPEDEF TUPLE <VERTEX<Transaction> txn_vertex, DATETIME ts> DeviceTxn;
    HeapAccum<DeviceTxn>(20, ts DESC) @@recent_device_txns;
    
    // Start from all transactions, traverse forward, and filter for the target device vertex
    Seed = {Transaction.*};
    Txns = SELECT s FROM Seed:s -(FROM_DEVICE:e)-> DeviceProfile:t 
           WHERE t == in_device_node
           ACCUM @@recent_device_txns += DeviceTxn(s, s.ts);
           
    PRINT @@recent_device_txns AS associated_transactions;
}

// 2. Card History Fix (Direct vertex parameterization)
CREATE OR REPLACE QUERY get_card_history(VERTEX<Card> in_card_node, INT limit_txns = 50) FOR GRAPH FraudGraph {
    TYPEDEF TUPLE <
        VERTEX<Transaction> txn_vertex, 
        DOUBLE amt, 
        DATETIME ts, 
        STRING channel, 
        DOUBLE risk_score, 
        STRING product_cd, 
        STRING addr1
    > TxnRecord;
    
    HeapAccum<TxnRecord>(limit_txns, ts DESC) @@recent_txns;

    StartCard = {in_card_node};

    Txns = SELECT t FROM StartCard:s -(MADE:e)-> Transaction:t
           ACCUM @@recent_txns += TxnRecord(t, t.amt, t.ts, t.channel, t.risk_score, t.product_cd, t.addr1);

    PRINT @@recent_txns AS history;
}

INSTALL QUERY get_device_neighbors, get_card_history'''


def main() -> int:
    conn = tg.TigerGraphConnection(
        host=os.environ.get("TG_HOST"),
        graphname=os.environ.get("TG_GRAPH"),
        username=os.environ.get("TG_USERNAME"),
        password=os.environ.get("TG_PASSWORD"),
    )

    print("Generating bearer token...")
    conn.getToken()

    print("Installing remaining LangGraph agent queries...")
    result = conn.gsql(GSQL_BLOCK)
    print("Installation response:")
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
