# TigerGraph Fraud Investigation Agent

An agentic fraud investigation system built for the TigerGraph Hackathon. The
system loads the IEEE-CIS-derived benchmark data into TigerGraph, retrieves
transaction and investigation evidence through the TigerGraph MCP server, and
uses a LangGraph workflow with Google Gemini to produce one structured answer
file for each benchmark case.

## Project Layout

- `data/`: dataset README and local benchmark data files
- `schema/`: TigerGraph GSQL schema definitions
- `scripts/`: connection, schema, query, authentication, and data-loading utilities
- `agent/`: LangGraph state machine, prompts, MCP-backed tools, and state definitions
- `cases/`: generated JSON answer files for the 20 benchmark cases

## Setup

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install the project dependencies:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in the TigerGraph and model credentials:

```bash
cp .env.example .env
```

The active environment should provide `TG_HOST`, `TG_GRAPH`, `TG_USERNAME`,
`TG_PASSWORD`, `GEMINI_API_KEY`, and `TIGERGRAPH_MCP_COMMAND` or
`TIGERGRAPH_MCP_URL`.

Raw CSV files are intentionally excluded from Git because the transaction file
is large. Place the benchmark data files in `data/` before loading or running
the agent.

## Execution

Run the complete benchmark batch from the project root:

```bash
source venv/bin/activate
python run_investigation.py
```

The runner processes `data/case_pack.csv`, skips case outputs that already
exist, and writes one JSON result per case to `cases/<case_id>.json`.

## Verification

The final output set contains 20 JSON files. Each output includes the case
assessment, structured graph evidence, SAR fields, initial and final action
recommendations, approval routes, tool-call metadata, latency, and graph
write-back identifiers.
