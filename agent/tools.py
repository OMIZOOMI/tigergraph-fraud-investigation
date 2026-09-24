"""LangGraph tools for MCP evidence retrieval and TigerGraph case persistence."""

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any, TypedDict

import pandas as pd
import pyTigerGraph as tg
from dotenv import find_dotenv, load_dotenv
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient


load_dotenv(find_dotenv(), override=True)

conn = tg.TigerGraphConnection(
    host=os.environ.get("TG_HOST"),
    graphname=os.environ.get("TG_GRAPH"),
    username=os.environ.get("TG_USERNAME"),
    password=os.environ.get("TG_PASSWORD"),
)
conn.getToken()


class InvestigationState(TypedDict, total=False):
    """Normalized state and final answer fields for one investigation."""

    case_id: str
    card_id: str
    trigger_context: dict
    status: str
    verdict: str
    evidence: list[dict]
    fraud_probability: float
    pattern: str
    pattern_description: str
    affected_txn_ids: list[str]
    first_suspicious_txn_id: str
    connected_card_ids: list[str]
    connected_device_profiles: list[str]
    exposure_usd: float
    similar_prior_cases: list[str]
    summary: str
    written_to_graph: bool
    graph_case_id: str
    evidence_requests: list[dict]
    next_best_actions: dict
    next_best_action: str
    sar: dict
    stop_reason: str
    tool_calls: int
    tokens: int
    latency_s: float


def _mcp_config() -> dict[str, dict[str, Any]]:
    """Build the TigerGraph MCP transport configuration from environment."""
    url = os.environ.get("TIGERGRAPH_MCP_URL", "").strip()
    command = os.environ.get("TIGERGRAPH_MCP_COMMAND", "").strip()
    if url:
        config: dict[str, Any] = {
            "transport": os.environ.get("TIGERGRAPH_MCP_TRANSPORT", "streamable_http"),
            "url": url,
        }
        token = os.environ.get("TG_PASSWORD", "").strip()
        if token:
            config["headers"] = {"Authorization": f"Bearer {token}"}
        return {"tigergraph": config}
    if command:
        parts = shlex.split(command)
        return {
            "tigergraph": {
                "transport": "stdio",
                "command": parts[0],
                "args": parts[1:],
            }
        }
    return {}


async def _call_mcp_query(query_name: str, arguments: dict[str, Any]) -> Any:
    config = _mcp_config()
    if not config:
        raise RuntimeError(
            "TigerGraph MCP is not configured. Set TIGERGRAPH_MCP_URL "
            "or TIGERGRAPH_MCP_COMMAND."
        )
    client = MultiServerMCPClient(config)
    tools = await client.get_tools()
    selected = next(
        (candidate for candidate in tools if candidate.name == "tigergraph__run_installed_query"),
        None,
    )
    if selected is None:
        raise RuntimeError("TigerGraph MCP run_installed_query tool is unavailable")
    return await selected.ainvoke({
        "graph_name": os.environ.get("TG_GRAPH"),
        "query_name": query_name,
        "params": arguments,
    })


def _run_mcp_query(tool_name: str, arguments: dict[str, Any]) -> str:
    try:
        response = asyncio.run(_call_mcp_query(tool_name, arguments))
        if isinstance(response, list):
            response = "\n".join(
                item.get("text", "")
                for item in response
                if isinstance(item, dict) and item.get("text")
            )
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except json.JSONDecodeError:
                pass
        return f"{tool_name}: {json.dumps(response, default=str)}"
    except Exception as exc:  # noqa: BLE001
        return f"{tool_name}: MCP query failed: {exc}"


@tool
def tool_get_card_history(card_id: str) -> str:
    """Retrieve card transaction history through the TigerGraph MCP server."""
    return _run_mcp_query("get_card_history", {"in_card_node": card_id})


@tool
def tool_get_device_neighbors(device_id: str) -> str:
    """Retrieve device-associated transactions through TigerGraph MCP."""
    return _run_mcp_query("get_device_neighbors", {"in_device_node": device_id})


@tool
def tool_find_similar_cases(pattern: str, min_exposure: float = 0.0) -> str:
    """Retrieve historical closed-case precedent through TigerGraph MCP."""
    return _run_mcp_query(
        "find_similar_cases",
        {"in_pattern": pattern, "min_exposure": min_exposure},
    )


@tool
def tool_get_local_similar_cases(card_id: str, customer_id: str) -> str:
    """Find prior closed cases sharing the current card or customer locally."""
    history_path = Path(__file__).resolve().parents[1] / "data" / "closed_cases_history.csv"
    history = pd.read_csv(history_path, usecols=["case_id", "customer_id", "card_id", "outcome", "analyst_notes"])
    card_id = str(card_id or "").strip()
    customer_id = str(customer_id or "").strip()
    matches = pd.Series(False, index=history.index)
    if card_id:
        matches |= history["card_id"].fillna("").astype(str).eq(card_id)
    if customer_id:
        matches |= history["customer_id"].fillna("").astype(str).eq(customer_id)

    matched = history.loc[matches].drop_duplicates(subset=["case_id"])
    results = [
        {
            "case_id": str(row["case_id"]),
            "outcome": str(row["outcome"]),
            "summary": str(row["analyst_notes"]),
        }
        for _, row in matched.iterrows()
    ]
    return json.dumps(
        {
            "similar_prior_cases": [item["case_id"] for item in results],
            "matches": results,
        },
        default=str,
    )


@tool
def tool_write_investigation_result(
    case_id: str,
    verdict: str,
    fraud_prob: float,
    pattern: str,
    exposure: float,
    summary: str,
    status: str = "open",
    pattern_description: str = "",
    opened_at: str = "",
    closed_at: str = "",
) -> str:
    """Upsert the final InvestigationCase vertex into TigerGraph."""
    attributes = {
        "status": status,
        "verdict": verdict,
        "fraud_probability": fraud_prob,
        "pattern": pattern,
        "pattern_description": pattern_description,
        "exposure_usd": exposure,
        "summary": summary,
        "written_to_graph": True,
        "opened_at": opened_at,
        "closed_at": closed_at,
    }
    result = conn.upsertVertices(
        "InvestigationCase",
        [[case_id, attributes]],
    )
    return json.dumps(
        {
            "written_to_graph": True,
            "graph_case_id": case_id,
            "upsert_result": result,
        },
        default=str,
    )
