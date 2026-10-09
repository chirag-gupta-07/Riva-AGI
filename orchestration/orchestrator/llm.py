"""Bounded Gemini calls with validated, audited tool execution."""
import functools
import inspect
import os
import time
import uuid
from typing import Optional, List, Callable, Tuple, Union

try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
except ImportError:
    genai = types = None
    APIError = Exception

from orchestration.tools import tool_registry
from orchestration.tools.policy import check_budget, current_policy
from orchestration.orchestrator.schemas.tool import ToolCall, ToolResult
from orchestration.orchestrator.config import key_manager

PRIMARY_MODEL = 'gemini-3.5-flash-lite'
FALLBACK_MODELS = [m.strip() for m in os.getenv('GEMINI_FALLBACK_MODELS', '').split(',') if m.strip()]
MODEL_MAPPING = {}


class LLMExecutionError(RuntimeError):
    def __init__(self, message, tool_calls=None):
        super().__init__(message)
        self.tool_calls = tool_calls or []


def _is_audio_model(model: str) -> bool:
    return any(marker in model.lower() for marker in ('-live', 'native-audio', '-tts'))


def resolve_text_model(agent_id: str = '') -> str:
    """Resolve at call time so a shared voice setting cannot select the Live API."""
    for setting in (f'GEMINI_MODEL_{agent_id.upper()}', 'GEMINI_TEXT_MODEL'):
        model = os.getenv(setting, '').strip()
        if model:
            if _is_audio_model(model):
                raise LLMExecutionError(
                    f'{setting} selects an audio/Live model ({model}). '
                    'Choose a text-generation model; use GEMINI_LIVE_MODEL for voice.')
            return model
    mapped = MODEL_MAPPING.get(agent_id)
    if mapped:
        if _is_audio_model(mapped):
            raise LLMExecutionError(f'Text agent {agent_id} is mapped to an audio/Live model.')
        return mapped
    # Backwards compatibility for a shared text model. Existing voice .env files
    # can keep GEMINI_MODEL without breaking chat.py.
    legacy = os.getenv('GEMINI_MODEL', '').strip()
    return legacy if legacy and not _is_audio_model(legacy) else PRIMARY_MODEL


def _wrap_tool_for_execution(name: str, func: Callable, execution_log: list) -> Callable:
    @functools.wraps(func)
    def tracked_tool(*args, **kwargs):
        started = time.monotonic()
        parameters = {}
        try:
            bound = inspect.signature(func).bind(*args, **kwargs)
            bound.apply_defaults()
            parameters = dict(bound.arguments)
            if tool_registry.get_tool(name) is func:
                result = tool_registry.execute_result(name, **parameters)
            else:
                check_budget()
                policy = current_policy()
                if policy.allowed_tools is not None and name not in policy.allowed_tools:
                    raise PermissionError(f'Tool {name} is not permitted.')
                if len(policy.calls) >= policy.max_calls:
                    raise RuntimeError('Tool call budget exhausted.')
                policy.calls.append(name)
                from pydantic import validate_call
                output = validate_call(config={'strict': True})(func)(**parameters)
                result = ToolResult(call_id=uuid.uuid4().hex, tool_name=name, success=True, output=output)
        except Exception as exc:
            result = ToolResult(call_id=uuid.uuid4().hex, tool_name=name, success=False,
                                error=f'Tool Execution Error ({name}): {exc}')
        result.execution_time_ms = (time.monotonic() - started) * 1000
        execution_log.append(ToolCall(call_id=result.call_id, tool_name=name, parameters=parameters,
                                     expected_return_type=type(result.output).__name__, result=result))
        return result.output if result.success else result.error
    tracked_tool.__name__ = name
    return tracked_tool


def call_gemini(prompt: str, api_key: str, system_instruction: str, agent_id: str,
                tools: Optional[List[Union[str, Callable]]] = None,
                return_tool_calls: bool = False, response_schema=None
                ) -> Union[str, Tuple[str, List[ToolCall]]]:
    if genai is None or types is None:
        raise LLMExecutionError('Missing google-genai. Install requirements.txt first.')
    api_key = api_key or key_manager.get_api_key_for_role(agent_id)
    if not api_key:
        raise LLMExecutionError(f'API key is missing for agent [{agent_id}].')
    executed = []
    wrapped = []
    for item in tools or []:
        func = tool_registry.get_tool(item) if isinstance(item, str) else item
        if not callable(func):
            raise LLMExecutionError(f'Unknown tool: {item}')
        name = item if isinstance(item, str) else func.__name__
        wrapped.append(_wrap_tool_for_execution(name, func, executed))
    config_args = dict(system_instruction=system_instruction, temperature=0.2,
                       tools=wrapped or None,
                       automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=20))
    if response_schema is not None:
        config_args.update(response_mime_type='application/json', response_schema=response_schema)
    config = types.GenerateContentConfig(**config_args)
    primary = resolve_text_model(agent_id)
    models = list(dict.fromkeys([primary] + [m for m in FALLBACK_MODELS if not _is_audio_model(m)]))[:3]
    last_error = None
    for attempt, model in enumerate(models):
        check_budget()
        if attempt:
            time.sleep(min(2 ** attempt, 4))
        client = None
        try:
            remaining = max(1, min(60, current_policy().deadline - time.monotonic()))
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
                timeout=int(remaining * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
            chat = client.chats.create(model=model, config=config)
            response = chat.send_message(prompt)
            check_budget()
            if getattr(response, 'function_calls', None):
                raise LLMExecutionError('Tool loop stopped before completion; action budget may be exhausted.', executed)
            content = response.text
            if not content or not content.strip():
                raise LLMExecutionError('Model returned no final answer.', executed)
            failures = [c for c in executed if c.result and not c.result.success]
            if failures:
                raise LLMExecutionError('Task encountered tool failures; inspect the tool trace before retrying. ' +
                                        failures[-1].result.error, executed)
            return (content, executed) if return_tool_calls else content
        except LLMExecutionError:
            raise
        except APIError as exc:
            last_error = exc
            if executed:
                raise LLMExecutionError('LLM failed after tool execution. Automatic replay was blocked.', executed) from exc
            if getattr(exc, 'code', None) not in {404, 429, 500, 502, 503, 504}:
                break
        except Exception as exc:
            raise LLMExecutionError(f'LLM request failed: {type(exc).__name__}', executed) from exc
        finally:
            if client is not None:
                client.close()
    status = getattr(last_error, 'code', 'unknown')
    hints = {
        400: 'check model compatibility and request configuration',
        429: 'Gemini rate/quota limit reached; wait for the limit to reset or check this project\'s quota',
        401: 'check the configured API key',
        403: 'check API key permissions and model access',
        404: 'the configured model is unavailable; check GEMINI_TEXT_MODEL',
    }
    hint = hints.get(status, 'check model access and service availability')
    raise LLMExecutionError(f'LLM request failed for {model} (status {status}); {hint}.', executed)
