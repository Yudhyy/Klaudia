# Klaudia agent

One main agent loads versioned skills and calls bounded tools for owned resources.
The application supplies authenticated identity, durable tasks and checked ledger
operations. Provider model construction lives in `core/agent/llm.py`.
