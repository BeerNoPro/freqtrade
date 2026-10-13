"""
Market Context Engine (MCE): research toolkit for named, testable market hooks.

Layers: point-in-time store (archive, universe, pit, live) -> features -> regime -> hooks ->
event study -> forward test (forward, journal); portfolio for daily target-weight books. The
product layer (riskbook, messages, outbox, product) reads the journal and never writes to it.
Every function except those in ``labels`` must be causal: the value at row t may only use
rows <= t. ``labels`` is the single place allowed to look forward (to build outcomes).

Generic code only. Concrete hooks, thresholds and results live in ``user_data/research/``.
"""
