"""
Market Context Engine (MCE): research toolkit for named, testable market hooks.

Layers: features -> regime -> hooks -> event study / assessment -> journal.
Every function except those in ``labels`` must be causal: the value at row t may only use
rows <= t. ``labels`` is the single place allowed to look forward (to build outcomes).

Generic code only. Concrete hooks, thresholds and results live in ``user_data/research/``.
"""
