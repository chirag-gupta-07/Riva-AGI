"""Deterministic graph tests: no live LLM, shell actions, or external requests."""
import json
from unittest.mock import Mock
import pytest
from orchestration import AgentResponse, ResponseStatus
from orchestration.orchestrator.main import run_orchestrator, registry, task_manager


def response(agent, content):
    return AgentResponse(agent_id=agent, content=content)


@pytest.fixture
def handlers(monkeypatch):
    handlers = {}
    original = registry.get_agent
    monkeypatch.setattr(registry, 'get_agent', lambda name: handlers.get(name) or original(name))
    return handlers


def test_coding_task_orchestration(handlers):
    handlers['intent_classifier'] = Mock(return_value=response('intent_classifier', '{"complexity":"simple","target_agent":"coder","intent":"coding","confidence":1}'))
    handlers['coder'] = Mock(return_value=response('coder', 'def sort(items): return sorted(items)'))
    result = run_orchestrator('Draft a Python function for sorting')
    assert result['agent'] == 'coder'
    assert result['response_payload'].status == ResponseStatus.SUCCESS
    assert task_manager.get_task_status(result['task_id']).status == 'completed'


def test_unknown_task_uses_fallback(handlers):
    handlers['intent_classifier'] = Mock(return_value=response('intent_classifier', 'invalid'))
    result = run_orchestrator('xyz random task')
    assert result['agent'] == 'fallback'
    assert result['response_payload'].status == ResponseStatus.FAILURE


def complex_handlers(handlers):
    handlers['intent_classifier'] = Mock(return_value=response('intent_classifier', '{"complexity":"complex","target_agent":"planner","intent":"research","confidence":1}'))
    handlers['planner'] = Mock(return_value=response('planner', json.dumps([
        {'agent': 'researcher', 'task': 'Find the evidence'}, {'agent': 'writer', 'task': 'Write using the evidence'}])))
    handlers['researcher'] = Mock(return_value=response('researcher', 'Evidence: 42'))
    handlers['writer'] = Mock(return_value=response('writer', 'Final report: 42'))
    handlers['reviewer'] = Mock(return_value=response('reviewer', '{"status":"approved","feedback":"Evidence verified"}'))


def test_steps_receive_context_and_preserve_deliverable(handlers):
    complex_handlers(handlers)
    result = run_orchestrator('Research and write a report', session_id='context-test')
    context = json.loads(handlers['writer'].call_args.args[0].text_content)
    assert context['assigned_step'] == 'Write using the evidence'
    assert context['previous_results'][0]['result'] == 'Evidence: 42'
    assert result['current_step'] == 2
    assert result['response_payload'].content == 'Final report: 42'
    handlers['writer'].assert_called_once()


@pytest.mark.parametrize('invalid_review', ['not JSON', '{}', '{"status":"maybe","feedback":"?"}'])
def test_review_fails_closed(handlers, invalid_review):
    complex_handlers(handlers)
    handlers['reviewer'].return_value = response('reviewer', invalid_review)
    result = run_orchestrator('Research and write')
    assert result['response_payload'].status == ResponseStatus.FAILURE
    assert result['error'].startswith('Review failed')


def test_repair_does_not_replay_workers(handlers):
    complex_handlers(handlers)
    handlers['reviewer'].return_value = response('reviewer', '{"status":"rejected","feedback":"Explain limitations"}')
    handlers['reasoner'] = Mock(return_value=response('reasoner', 'Unresolved limitations'))
    result = run_orchestrator('Research and write')
    assert result['response_payload'].status == ResponseStatus.FAILURE
    assert result['review_attempts'] == 1
    handlers['researcher'].assert_called_once()
    handlers['writer'].assert_called_once()
    repair = json.loads(handlers['reasoner'].call_args.args[0].text_content)
    assert repair['review_feedback'] == 'Explain limitations'


def test_worker_failure_stops_dependents(handlers):
    complex_handlers(handlers)
    handlers['researcher'].return_value = AgentResponse(agent_id='researcher', status=ResponseStatus.FAILURE, error_message='network failure')
    result = run_orchestrator('Research and write')
    handlers['writer'].assert_not_called()
    handlers['reviewer'].assert_not_called()
    assert result['response_payload'].status == ResponseStatus.FAILURE


@pytest.mark.parametrize('plan', ['[]', '{}', '[{"agent":"executor","task":"oops"}]'])
def test_invalid_plan_stops(handlers, plan):
    complex_handlers(handlers)
    handlers['planner'].return_value = response('planner', plan)
    result = run_orchestrator('Research and write')
    assert result['response_payload'].status == ResponseStatus.FAILURE
    handlers['researcher'].assert_not_called()


def test_invalid_manager_route_uses_fallback(handlers):
    handlers['intent_classifier'] = Mock(return_value=response('intent_classifier', '{"complexity":"simple","target_agent":"reviewer","intent":"x","confidence":1}'))
    result = run_orchestrator('task')
    assert result['response_payload'].status == ResponseStatus.FAILURE
