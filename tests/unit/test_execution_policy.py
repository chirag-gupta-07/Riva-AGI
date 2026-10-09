import socket
import time
from unittest.mock import Mock, patch
import pytest
from orchestration.tools import ToolRegistry, tool_registry
from orchestration.tools.policy import execution_scope, current_policy, workspace_path
from orchestration.tools.network import validate_url, bounded_read, CheckedRedirect
from orchestration.tools.builtin.system_tools import execute_command
from orchestration.tools.builtin.file_tools import write_file
from orchestration.orchestrator import llm


def test_path_escape_and_credentials_blocked(tmp_path):
    for path in [str(tmp_path.parent / 'outside.txt'), '../outside.txt', '.env', '.git/config', '.ssh/id_rsa']:
        with pytest.raises(PermissionError):
            workspace_path(path)
    assert workspace_path('nested/a.txt') == tmp_path / 'nested/a.txt'


def test_overwrites_require_explicit_choice(tmp_path):
    path = str(tmp_path / 'a.txt')
    assert 'Success' in write_file(path, 'original')
    assert 'Error' in write_file(path, 'replacement')
    assert (tmp_path / 'a.txt').read_text() == 'original'


def test_error_words_in_document_are_successful_data(tmp_path):
    (tmp_path / 'log.txt').write_text('Error handling guide: this is document content.')
    result = tool_registry.execute_result('read_file', file_path='log.txt')
    assert result.success
    assert result.output.startswith('Error handling guide')
    missing = tool_registry.execute_result('read_file', file_path='missing.txt')
    assert not missing.success


def test_shell_requires_authorization():
    with patch('subprocess.run') as run:
        with pytest.raises(PermissionError):
            execute_command('echo hello')
        run.assert_not_called()


def test_tool_argument_validation_and_permissions():
    tools = ToolRegistry()
    @tools.register
    def count(value: int):
        return value + 1
    assert not tools.execute_result('count', value='1').success
    with execution_scope(allowed_tools=frozenset()):
        assert not tools.execute_result('count', value=1).success
    with execution_scope(max_calls=0):
        assert not tools.execute_result('count', value=1).success


def test_cancellation_and_deadline():
    with execution_scope(deadline=time.monotonic() - 1):
        result = tool_registry.execute_result('get_system_info')
        assert not result.success and 'deadline' in result.error
    current_policy().cancelled.set()
    result = tool_registry.execute_result('get_system_info')
    assert not result.success and 'cancelled' in result.error


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1'])
def test_private_network_blocked(address):
    with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 80))]):
        with pytest.raises(PermissionError):
            validate_url('http://example.test')


def test_redirect_and_response_limits():
    with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]):
        with pytest.raises(PermissionError):
            CheckedRedirect().redirect_request(None, None, 302, '', {}, 'http://localhost/private')
    response = Mock()
    response.read.return_value = b'x' * 2_000_001
    with pytest.raises(ValueError):
        bounded_read(response)


def test_no_llm_replay_after_tool_action(monkeypatch):
    client = Mock()
    chat = Mock()
    client.chats.create.return_value = chat
    monkeypatch.setattr(llm.genai, 'Client', Mock(return_value=client))
    monkeypatch.setattr(llm, 'FALLBACK_MODELS', ['other-model'])
    effects = []
    def act(value: str) -> str:
        effects.append(value)
        return 'done'
    def send(prompt):
        config = client.chats.create.call_args.kwargs['config']
        config.tools[0](value='once')
        raise llm.APIError(503, {'error': {'message': 'unavailable', 'status': 'UNAVAILABLE'}})
    chat.send_message.side_effect = send
    with pytest.raises(llm.LLMExecutionError) as caught:
        llm.call_gemini('act', 'fake', 'test', 'test', tools=[act])
    assert effects == ['once']
    assert len(caught.value.tool_calls) == 1
    assert caught.value.tool_calls[0].result.success
    assert chat.send_message.call_count == 1


def test_missing_sdk_fails_clearly(monkeypatch):
    monkeypatch.setattr(llm, 'types', None)
    with pytest.raises(llm.LLMExecutionError, match='Missing google-genai'):
        llm.call_gemini('x', 'fake', 'x', 'x')
