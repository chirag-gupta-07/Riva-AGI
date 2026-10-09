from unittest.mock import MagicMock, patch
import pytest
from orchestration.orchestrator import llm


@pytest.fixture(autouse=True)
def clean_models(monkeypatch):
    for name in ('GEMINI_MODEL', 'GEMINI_TEXT_MODEL', 'GEMINI_MODEL_REASONER', 'GEMINI_LIVE_MODEL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, 'MODEL_MAPPING', {})


@pytest.mark.parametrize('model', ['gemini-3.1-flash-live-preview', 'gemini-2.5-flash-native-audio-latest'])
def test_shared_voice_model_does_not_reach_text_api(monkeypatch, model):
    monkeypatch.setenv('GEMINI_MODEL', model)
    client = MagicMock()
    client.chats.create.return_value.send_message.return_value = MagicMock(text='Hello!', function_calls=None)
    with patch.object(llm.genai, 'Client', return_value=client):
        assert llm.call_gemini('Hello', 'fake-key', 'Respond briefly', 'reasoner') == 'Hello!'
    assert client.chats.create.call_args.kwargs['model'] == llm.PRIMARY_MODEL


def test_text_and_agent_overrides_take_precedence(monkeypatch):
    monkeypatch.setenv('GEMINI_MODEL', 'legacy-text-model')
    assert llm.resolve_text_model('reasoner') == 'legacy-text-model'
    monkeypatch.setenv('GEMINI_TEXT_MODEL', 'dedicated-text-model')
    assert llm.resolve_text_model('reasoner') == 'dedicated-text-model'
    monkeypatch.setenv('GEMINI_MODEL_REASONER', 'agent-text-model')
    assert llm.resolve_text_model('reasoner') == 'agent-text-model'


@pytest.mark.parametrize('setting', ['GEMINI_TEXT_MODEL', 'GEMINI_MODEL_REASONER'])
def test_explicit_audio_override_has_actionable_error(monkeypatch, setting):
    monkeypatch.setenv(setting, 'gemini-3.1-flash-live-preview')
    with patch.object(llm.genai, 'Client') as client:
        with pytest.raises(llm.LLMExecutionError, match='GEMINI_LIVE_MODEL'):
            llm.call_gemini('Hello', 'fake', 'Respond', 'reasoner')
        client.assert_not_called()


def test_voice_has_separate_model_setting(monkeypatch):
    from voice_speech.engine.config.settings import GeminiLiveConfig
    monkeypatch.setenv('GEMINI_MODEL', 'legacy-model')
    monkeypatch.setenv('GEMINI_TEXT_MODEL', 'text-model')
    monkeypatch.setenv('GEMINI_LIVE_MODEL', 'dedicated-live-model')
    assert GeminiLiveConfig().model == 'dedicated-live-model'
    monkeypatch.delenv('GEMINI_LIVE_MODEL')
    assert GeminiLiveConfig().model == 'legacy-model'


def test_quota_error_is_distinct_from_model_configuration(monkeypatch):
    monkeypatch.setattr(llm, 'FALLBACK_MODELS', [])
    client = MagicMock()
    client.chats.create.return_value.send_message.side_effect = llm.APIError(
        429, {'error': {'message': 'Quota exceeded', 'status': 'RESOURCE_EXHAUSTED'}})
    with patch.object(llm.genai, 'Client', return_value=client):
        with pytest.raises(llm.LLMExecutionError, match='rate/quota limit reached'):
            llm.call_gemini('Hello', 'fake', 'Respond', 'reasoner')
