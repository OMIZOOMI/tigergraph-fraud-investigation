import json
import os
import time

import pandas as pd
from dotenv import find_dotenv, load_dotenv
from langgraph.errors import GraphRecursionError

load_dotenv(find_dotenv(), override=True)

from agent.graph import agent_app


os.makedirs("cases", exist_ok=True)

df = pd.read_csv("data/case_pack.csv")
target_case_id = os.environ.get("CASE_ID")


def safe_text(value):
    return "" if pd.isna(value) else str(value)


def safe_score(value):
    return None if pd.isna(value) else float(value)


def build_output(case_id, state, latency_s):
    evidence = state.get("evidence", [])
    if isinstance(evidence, str):
        evidence = [{
            "claim": evidence,
            "source": "graph",
            "ref": "agent",
            "entity_ids": [],
        }]
    actions = state.get("next_best_actions", {})
    if not isinstance(actions, dict):
        actions = {}
    final_actions = actions.get("final", [])
    if isinstance(final_actions, str):
        final_actions = [{
            "action": final_actions,
            "route": "L1",
            "reason": "Agent recommendation.",
        }]
    sar = state.get("sar", {})
    if not isinstance(sar, dict):
        sar = {}
    sar = {
        "file": bool(sar.get("file", False)),
        "reason": str(sar.get("reason", "")),
        "narrative": str(sar.get("narrative", "")),
        "subjects": sar.get("subjects", []),
        "total_amount_usd": sar.get("total_amount_usd", 0.0),
        "activity_dates": sar.get("activity_dates", []),
    }
    return {
        "case_id": case_id,
        "case": {
            "status": state.get("status", "open"),
            "verdict": state.get("verdict", "uncertain"),
            "fraud_probability": state.get("fraud_probability", 0.0),
            "pattern": state.get("pattern", "none"),
            "pattern_description": state.get("pattern_description", ""),
            "affected_txn_ids": state.get("affected_txn_ids", []),
            "first_suspicious_txn_id": state.get("first_suspicious_txn_id", ""),
            "connected_card_ids": state.get("connected_card_ids", []),
            "connected_device_profiles": state.get("connected_device_profiles", []),
            "exposure_usd": state.get("exposure_usd", 0.0),
            "evidence": evidence,
            "similar_prior_cases": state.get("similar_prior_cases", []),
            "summary": state.get("summary", ""),
            "written_to_graph": bool(state.get("written_to_graph", False)),
            "graph_case_id": state.get("graph_case_id", ""),
        },
        "evidence_requests": state.get("evidence_requests", []),
        "next_best_actions": {
            "initial": actions.get("initial", final_actions),
            "final": final_actions,
            "what_changed": actions.get("what_changed", "nothing"),
        },
        "sar": sar,
        "stop_reason": state.get("stop_reason", "Investigation completed."),
        "tool_calls": int(state.get("tool_calls", 0)),
        "tokens": int(state.get("tokens", 0)),
        "latency_s": round(latency_s, 3),
    }

for index, (_, row) in enumerate(df.iterrows()):
    case_id = str(row["case_id"])
    if target_case_id and case_id != target_case_id:
        continue
    card_id = str(row["card_id"])
    output_path = os.path.join("cases", f"{case_id}.json")
    if os.path.exists(output_path) and not target_case_id:
        print(f"Skipping {case_id}; output already exists.")
        continue
    print(f"Processing case {index + 1} of {len(df)}: {case_id}...")
    case_started = time.perf_counter()

    trigger_context = {
        "trigger_type": safe_text(row.get("trigger_type", "")),
        "trigger_text": safe_text(row.get("trigger_text", "")),
        "customer_id": safe_text(row.get("customer_id", "")),
        "flagged_txn_id": safe_text(row.get("flagged_txn_id", "")),
        "initial_risk_score": safe_score(row.get("risk_score", float("nan"))),
        "opened_at": safe_text(row.get("opened_at", "")),
    }

    for attempt in range(3):
        try:
            final_state = agent_app.invoke({
                "case_id": case_id,
                "card_id": card_id,
                "trigger_context": trigger_context,
                "evidence": "",
                "evidence_requests": [],
                "pattern": "",
                "next_best_action": "",
                "fraud_probability": 0.0,
            })

            output = build_output(
                case_id,
                final_state,
                time.perf_counter() - case_started,
            )

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=4)

            with open(output_path, "r", encoding="utf-8") as f:
                print(f.read())
            time.sleep(2)
            break
        except GraphRecursionError:
            fallback_output = build_output(case_id, {
                "status": "escalated",
                "verdict": "uncertain",
                "pattern": "undocumented",
                "pattern_description": "Investigation exceeded the graph recursion limit.",
                "fraud_probability": 0.5,
                "evidence": [{
                    "claim": "Investigation exceeded the graph recursion limit.",
                    "source": "graph",
                    "ref": "langgraph",
                    "entity_ids": [],
                }],
                "summary": "Manual review required after investigation timeout.",
                "next_best_actions": {
                    "initial": [{"action": "manual_review", "route": "L1", "reason": "Timeout."}],
                    "final": [{"action": "manual_review", "route": "L1", "reason": "Timeout."}],
                    "what_changed": "nothing",
                },
                "stop_reason": "Graph recursion limit reached.",
                "evidence_requests": [],
                "sar": {},
                "written_to_graph": False,
                "graph_case_id": "",
            }, time.perf_counter() - case_started)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(fallback_output, f, indent=4)
            print(f"Recursion limit reached for {case_id}; saved manual-review fallback.")
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                print(f"Failed to process {case_id} after 3 attempts: {exc}")
                break
            print(f"Temporary API error on {case_id}, retrying in 6 seconds...")
            time.sleep(6)
