"""Claim fixtures live once in ``tests/conftest.py`` (``claim_doubles``).

Explicit claim behavior for legacy launcher doubles, never a global bypass.
Dispatch modules opt in with
``pytestmark = pytest.mark.usefixtures("worker_claims")``.
"""
