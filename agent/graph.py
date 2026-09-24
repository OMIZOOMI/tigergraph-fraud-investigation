"""LangGraph state machine for fraud investigations."""

import os
import json
from datetime import datetime, timezone

from dotenv import find_dotenv, load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph

from agent.prompts import DECIDER_PROMPT, INVESTIGATOR_PROMPT
from agent.tools import (
    InvestigationState,
    tool_find_similar_cases,
    tool_get_card_history,
    tool_get_device_neighbors,
    tool_write_investigation_result,
)


load_dotenv(find_dotenv(), override=True)

llm = ChatGoogleGenerativeAI(
    model="gemini-3.5-flash-lite",
    api_key=os.environ.get("GEMINI_API_KEY"),
)
llm_with_tools = llm.bind_tools(
    [tool_get_card_history, tool_get_device_neighbors, tool_find_similar_cases]
)
READ_ONLY_TOOLS = {
    tool_get_card_history.name: tool_get_card_history,
    tool_get_device_neighbors.name: tool_get_device_neighbors,
    tool_find_similar_cases.name: tool_find_similar_cases,
}


def _message_text(message) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("text")
        )
    return str(content)


def _parse_json_object(text: str) -> dict:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start:end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def _run_investigator(state: InvestigationState):
    messages = [
        SystemMessage(content=INVESTIGATOR_PROMPT),
        HumanMessage(
            content=(
                f"Investigate case_id={state['case_id']} and "
                f"card_id={state['card_id']}.\n"
                f"Trigger context: {json.dumps(state.get('trigger_context', {}), default=str)}\n"
                "Gather evidence now and account for the trigger context."
            )
        ),
    ]
    response = None
    tool_count = 0
    for _ in range(5):
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        tool_calls = getattr(response, "tool_calls", []) or []
        if not tool_calls:
            break
        for tool_call in tool_calls:
            tool_count += 1
            tool_name = tool_call.get("name")
            tool = READ_ONLY_TOOLS.get(tool_name)
            if tool is None:
                result = f"Unknown read-only tool requested: {tool_name}"
            else:
                result = tool.invoke(tool_call.get("args", {}))
            messages.append(
                ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call["id"],
                )
            )

    return response, messages, tool_count


def investigate_node(state: InvestigationState) -> dict:
    """Gather graph evidence and assess the likely fraud pattern."""
    response, _, tool_count = _run_investigator(state)
    result = _parse_json_object(_message_text(response))
    evidence_text = str(result.get("evidence", _message_text(response)))
    probability = result.get("fraud_probability", 0.0)
    try:
        probability = max(0.0, min(1.0, float(probability)))
    except (TypeError, ValueError):
        probability = 0.0
    pattern = str(result.get("pattern", "undetermined"))
    verdict = "fraud" if probability >= 0.5 else "legitimate" if probability <= 0.15 else "uncertain"
    status = {
        "fraud": "closed_fraud",
        "legitimate": "closed_legitimate",
        "uncertain": "escalated",
    }[verdict]
    flagged_txn_id = state.get("trigger_context", {}).get("flagged_txn_id", "")
    evidence = [{
        "claim": evidence_text,
        "source": "graph",
        "ref": "TigerGraph MCP evidence tools",
        "entity_ids": [state["card_id"]] + ([flagged_txn_id] if flagged_txn_id else []),
    }]
    return {
        "status": status,
        "verdict": verdict,
        "evidence": evidence,
        "fraud_probability": probability,
        "pattern": pattern,
        "pattern_description": evidence_text if pattern == "undocumented" else "",
        "affected_txn_ids": [flagged_txn_id] if flagged_txn_id and verdict != "legitimate" else [],
        "first_suspicious_txn_id": flagged_txn_id,
        "connected_card_ids": [],
        "connected_device_profiles": [],
        "exposure_usd": 0.0,
        "similar_prior_cases": [],
        "summary": evidence_text,
        "written_to_graph": False,
        "graph_case_id": "",
        "evidence_requests": [],
        "sar": {
            "file": False,
            "reason": "No structured SAR decision was requested.",
            "narrative": "",
            "subjects": [],
            "total_amount_usd": 0.0,
            "activity_dates": [],
        },
        "stop_reason": "Investigation assessment complete.",
        "tool_calls": tool_count,
        "tokens": 0,
        "latency_s": 0.0,
    }


def decide_action_node(state: InvestigationState) -> dict:
    """Choose the appropriate response based on the investigation assessment."""
    response = llm.invoke(
        [
            SystemMessage(content=DECIDER_PROMPT),
            HumanMessage(
                content=(
                    f"case_id={state['case_id']}\n"
                    f"trigger_context={json.dumps(state.get('trigger_context', {}), default=str)}\n"
                    f"evidence={json.dumps(state['evidence'], default=str)}\n"
                    f"fraud_probability={state['fraud_probability']}\n"
                    f"pattern={state['pattern']}"
                )
            ),
        ]
    )
    result = _parse_json_object(_message_text(response))
    action = str(result.get("next_best_action", "Escalate for analyst review."))
    route = str(result.get("approval_route", "L1"))
    action_object = {
        "action": action,
        "route": route,
        "reason": "Based on the investigator assessment.",
    }
    return {
        "next_best_action": action,
        "next_best_actions": {
            "initial": [action_object],
            "final": [action_object],
            "what_changed": "nothing",
        },
    }


def save_and_close_node(state: InvestigationState) -> dict:
    """Write the current investigation result back to TigerGraph."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    result = tool_write_investigation_result.invoke(
        {
            "case_id": state["case_id"],
            "verdict": state.get("verdict", "uncertain"),
            "fraud_prob": state["fraud_probability"],
            "pattern": state["pattern"],
            "exposure": state.get("exposure_usd", 0.0),
            "summary": state.get("summary", ""),
            "status": state.get("status", "open"),
            "pattern_description": state.get("pattern_description", ""),
            "opened_at": state.get("trigger_context", {}).get("opened_at", now),
            "closed_at": now,
        }
    )
    try:
        write_result = json.loads(result)
    except (TypeError, json.JSONDecodeError):
        write_result = {}
    return {
        "written_to_graph": bool(write_result.get("written_to_graph", False)),
        "graph_case_id": str(write_result.get("graph_case_id", "")),
        "stop_reason": "Investigation saved to TigerGraph.",
    }


workflow = StateGraph(InvestigationState)
workflow.add_node("investigate_node", investigate_node)
workflow.add_node("decide_action_node", decide_action_node)
workflow.add_node("save_and_close_node", save_and_close_node)
workflow.add_edge(START, "investigate_node")
workflow.add_edge("investigate_node", "decide_action_node")
workflow.add_edge("decide_action_node", "save_and_close_node")
workflow.add_edge("save_and_close_node", END)

agent_app = workflow.compile()
