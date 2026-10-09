"""Per-request execution policy. This is an application boundary, not an OS sandbox."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional
import os
import threading
import time


@dataclass
class ExecutionPolicy:
    workspace: Path = field(default_factory=lambda: Path(os.getenv("RIVA_WORKSPACE", Path(__file__).resolve().parents[2])).resolve())
    session_id: str = "default"
    allowed_tools: Optional[frozenset] = None
    authorize: Optional[Callable[[str, dict], bool]] = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    deadline: float = field(default_factory=lambda: time.monotonic() + 300)
    max_calls: int = 40
    calls: list = field(default_factory=list)
    # Explicit host allowlist for local development; never supplied by the model.
    local_hosts: frozenset = field(default_factory=lambda: frozenset(filter(None, os.getenv("RIVA_LOCAL_HOSTS", "").split(","))))


_policy = ContextVar("riva_execution_policy", default=None)


def current_policy() -> ExecutionPolicy:
    return _policy.get() or ExecutionPolicy()


@contextmanager
def execution_scope(policy=None, **overrides):
    base = policy or current_policy()
    token = _policy.set(replace(base, **overrides) if overrides else base)
    try:
        yield _policy.get()
    finally:
        _policy.reset(token)


def check_budget():
    policy = current_policy()
    if policy.cancelled.is_set():
        raise RuntimeError("Task cancelled.")
    if time.monotonic() >= policy.deadline:
        raise TimeoutError("Task deadline exceeded.")


def require_authorization(name: str, arguments: dict):
    check_budget()
    approve = current_policy().authorize
    if approve is None or not approve(name, arguments):
        raise PermissionError(f"Authorization required for {name}; no action was performed.")


def workspace_path(value: str) -> Path:
    root = current_policy().workspace.resolve()
    raw = Path(value).expanduser()
    target = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if not target.is_relative_to(root):
        raise PermissionError("Path is outside the configured workspace.")
    relative = target.relative_to(root)
    if any(part.lower() in {".git", ".aws", ".ssh", ".codex", ".agents"} or
           (part.lower().startswith(".env") and part.lower() != ".env.example")
           for part in relative.parts):
        raise PermissionError("Access to credentials or control directories is blocked.")
    return target
