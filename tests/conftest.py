"""Tests use isolated workspaces and never consume configured Gemini credentials."""
import os
import pytest
from orchestration.tools.policy import ExecutionPolicy, execution_scope


@pytest.fixture(autouse=True)
def isolated_execution(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith('GEMINI_API_KEY'):
            monkeypatch.delenv(key)
    with execution_scope(ExecutionPolicy(workspace=tmp_path)):
        yield
