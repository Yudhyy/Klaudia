# Klaudia agent

One main agent loads versioned skills and calls bounded tools for owned resources.
The application supplies authenticated identity, durable tasks and checked ledger
operations. Provider model construction lives in `core/agent/llm.py`.

The wheel maps the existing flat source layout into the `klaudia` namespace and
includes runtime skill Markdown files. Editable installs use the checkout's
parent directory to resolve the same namespace. The packaging suite checks the
wheel paths so a source checkout cannot hide an incomplete installed package.
