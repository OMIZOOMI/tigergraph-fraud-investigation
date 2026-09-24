# TigerGraph Fraud Investigation Agent

An agentic fraud investigation system built for the TigerGraph Hackathon. It
combines graph evidence, local case memory, and Google Gemini reasoning to
investigate 20 benchmark cases and produce auditable recommendations.

## At A Glance

The system answers a practical question: **What happened, how confident are we,
and what should the bank do next?**

- It compares suspicious transactions with the cardholder's history.
- It looks for related devices, regions, transactions, and closed cases.
- It distinguishes fraud, legitimate activity, and cases that need human review.
- It can request simulated customer evidence and show how the recommendation changes.
- It writes the investigation result back to TigerGraph and saves a readable JSON answer file.

## Demo Video

[Google Drive demo folder](https://drive.google.com/drive/folders/1JQ2Rokt4nrLOKi20YA5Ro-9a9aopoS0u?usp=share_link)

## Accessible Design

Fraud investigation is often reviewed by people who should not have to read
Python or decode raw machine output. This project keeps the underlying JSON and
graph evidence for automation and auditability, while also producing
human-readable context for every case.

The output explains the finding in plain language, identifies the evidence that
supports it, shows relevant historical case IDs, and states the recommended
action and approval route. A non-technical reviewer can therefore understand
the decision without opening the source code or reconstructing the reasoning
from raw database responses.

### What A Stakeholder Sees

| Output | Plain-language meaning |
|---|---|
| `summary` | A short explanation of what the investigation found |
| `evidence` | The claims, sources, and IDs supporting the conclusion |
| `similar_prior_cases` | Closed cases that provide historical context |
| `evidence_requests` | What additional information was requested or assumed |
| `next_best_actions.initial` | The first recommendation before more evidence |
| `next_best_actions.final` | The recommendation after evidence is considered |
| `next_best_actions.what_changed` | Why the recommendation stayed the same or changed |
| `sar` | Whether a suspicious activity report is appropriate |

### A Quick Audit Path

1. Read `summary` to understand the case in ordinary language.
2. Check `evidence` and `similar_prior_cases` to verify the supporting context.
3. Compare the `initial` and `final` actions to see how new evidence affected the decision.
4. Use the graph IDs and JSON fields to trace the result back to TigerGraph when deeper review is needed.

## System Architecture

The following flow shows how source data becomes an investigation result. The
TigerGraph MCP client is the primary graph evidence path. The local pandas
fallback keeps closed-case memory available when the MCP precedent endpoint is
unavailable.

```mermaid
flowchart TB
    subgraph Inputs["1. Inputs and preparation"]
        casePack[(case_pack.csv)]
        transactions[(transactions.csv)]
        identity[(identity.csv)]
        schema[(schema.gsql)]
        loader[Data loading scripts]
        runner[run_investigation.py]
        transactions --> loader
        identity --> loader
        schema --> loader
        casePack --> runner
    end

    subgraph Orchestrator["2. LangGraph orchestrator"]
        investigator[Investigator node]
        decider[Decision node]
        evidenceLoop{Evidence needed?}
        simulate[Evidence simulation node]
        writer[Save and close node]
        investigator --> decider
        decider --> evidenceLoop
        evidenceLoop -->|yes| simulate
        simulate --> decider
        evidenceLoop -->|no| writer
    end

    subgraph Evidence["3. Evidence services"]
        mcp[TigerGraph MCP client]
        tigerGraph[(TigerGraph FraudGraph)]
        history[(closed_cases_history.csv)]
        fallback[Local pandas fallback]
        mcp --> tigerGraph
        history --> fallback
        mcp -. MCP precedent unavailable .-> fallback
        fallback --> investigator
    end

    subgraph Outputs["4. Stakeholder-ready outputs"]
        outputs[(cases / case_id.json)]
        audit[Summary, evidence, actions, and audit metadata]
        outputs --> audit
    end

    loader --> tigerGraph
    runner --> investigator
    investigator --> mcp
    writer --> tigerGraph
    writer --> outputs

    classDef input fill:#e8f1ff,stroke:#3264a8,stroke-width:2px,color:#172033
    classDef process fill:#eef8ee,stroke:#3f7f4f,stroke-width:2px,color:#172033
    classDef evidence fill:#fff5df,stroke:#b77b19,stroke-width:2px,color:#172033
    classDef output fill:#f3eaff,stroke:#7548a8,stroke-width:2px,color:#172033
    class casePack,transactions,identity,schema input
    class loader,runner,investigator,decider,evidenceLoop,simulate,writer process
    class mcp,tigerGraph,history,fallback evidence
    class outputs,audit output
```

### Component Responsibilities

- **Inputs:** case alerts, transaction history, identity data, graph schema, and closed-case history.
- **LangGraph orchestrator:** controls investigation, decision-making, evidence evolution, and graph write-back.
- **TigerGraph MCP client:** retrieves transaction and graph relationship evidence through standard MCP tools.
- **Local fallback layer:** matches the current card or customer against `closed_cases_history.csv` with pandas.
- **JSON generation:** writes one structured, stakeholder-readable answer for each case.

## Development Workflow

Feature work is developed in isolation, validated against the benchmark, merged
into `main`, and then followed by case regeneration when output behavior changes.

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 18, "rankSpacing": 24, "padding": 18}, "themeVariables": {"fontSize": "13px"}}}%%
flowchart TB
    subgraph P1["Phase 1: Feature Branching"]
        direction LR
        branch([Create branch])
        implement[Implement LangGraph / MCP]
        branch --> implement
    end

    subgraph P2["Phase 2: Local Validation"]
        direction LR
        syntax[Run syntax checks]
        isolated[Test isolated case]
        syntax --> isolated
    end

    subgraph P3["Phase 3: Batch Regeneration"]
        direction LR
        clear[Clear stale cases]
        batch[Run 20 benchmarks]
        outputs[(Generated cases)]
        schemaCheck{Validate schema?}
        clear --> batch --> outputs --> schemaCheck
    end

    subgraph P4["Phase 4: Integration"]
        direction LR
        review[Review JSON diffs]
        merge[Merge to main]
        review --> merge
    end

    implement --> syntax
    isolated --> clear
    schemaCheck -->|pass| review
    schemaCheck -->|fix| implement

    classDef action fill:#EEF2FF,stroke:#4F46E5,stroke-width:2px,color:#172033,font-size:14px
    classDef data fill:#FFF7ED,stroke:#C2410C,stroke-width:2px,color:#172033,font-size:14px
    classDef decision fill:#ECFDF5,stroke:#0F766E,stroke-width:2px,color:#172033,font-size:14px
    class branch,implement,syntax,isolated,clear,batch,review,merge action
    class outputs data
    class schemaCheck decision
```

Recommended workflow:

- Create a focused feature branch.
- Make the smallest behavior or documentation change needed.
- Run syntax checks and targeted smoke tests.
- Delete stale generated outputs when behavior changes.
- Regenerate and validate all 20 case files.
- Review the staged diff before merging or pushing to `main`.

## Project Layout

- `data/`: benchmark inputs, dataset documentation, and closed-case history
- `schema/`: TigerGraph GSQL schema definitions
- `scripts/`: connection, schema, query, authentication, and data-loading utilities
- `agent/`: LangGraph state machine, prompts, MCP tools, fallback tools, and state definitions
- `cases/`: generated JSON answer files for the 20 benchmark cases
- `run_investigation.py`: batch runner for the benchmark case pack

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

Keep credentials in `.env`. The file is ignored by Git and must never be
committed or hardcoded into Python source files.

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

To run one case while iterating:

```bash
CASE_ID=HHG-001 python -W ignore run_investigation.py
```

## Verification

The final output set contains 20 JSON files. Each output includes the case
assessment, structured graph evidence, historical case memory, SAR fields,
initial and final action recommendations, approval routes, tool-call metadata,
latency, and graph write-back identifiers.

Useful checks include:

```bash
python -m compileall -q agent run_investigation.py
git diff --check
```
