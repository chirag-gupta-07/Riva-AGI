from unittest.mock import Mock
from orchestration import AgentResponse, ResponseStatus
import chat


def test_chat_uses_final_failure_instead_of_old_success(monkeypatch, capsys):
    app = Mock()
    app.stream.return_value = iter([
        {'coder': {'completed_steps': [{'agent': 'coder', 'result': 'old success'}],
                   'response_payload': AgentResponse(agent_id='coder', content='old success')}},
        {'reviewer': {'response_payload': AgentResponse(agent_id='reviewer', status=ResponseStatus.FAILURE,
                                                       content='Review failed')}}])
    monkeypatch.setattr(chat, 'create_orchestrator', lambda: app)
    output = chat.execute_prompt('task', [], session_id='persisted', authorize=None)
    assert output == 'Review failed'
    assert app.stream.call_args.args[0]['session_id'] == 'persisted'
    assert 'STATUS: FAILURE' in capsys.readouterr().out


def test_direct_tools_accept_json_and_report_errors(capsys):
    result = chat.execute_direct_tool('read_file {"file_path":"../outside.txt"}', 'direct')
    assert not result.success
    assert 'outside' in result.error
