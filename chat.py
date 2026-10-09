"""
Riva-AGI Interactive Chat CLI & Universal Orchestration Testbed
===============================================================
A unified CLI to interact with the Riva-AGI LangGraph Multi-Agent Orchestrator,
test all autonomous agents, test all registered tools (builtin and custom),
and inspect API key rotation, latency, and tool execution traces.
"""

import os
import sys
import time
import uuid
import re
import json
import threading
import argparse
import subprocess
import importlib.util
from typing import Optional

# Ensure standard UTF-8 output encoding or fallback safely on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Ensure root workspace directory is on sys.path
WORKSPACE_ROOT = os.path.abspath(os.path.dirname(__file__))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from dotenv import load_dotenv
load_dotenv()

from orchestration.orchestrator.main import create_orchestrator, initial_state
from orchestration.orchestrator.registry import registry
from orchestration.orchestrator.config import key_manager
from orchestration.tools import tool_registry
from orchestration import InputData, InputType, AgentResponse, ResponseStatus
from orchestration.tools.policy import ExecutionPolicy, execution_scope
from orchestration.tools.browser_service import browser_service


def authorize_action(name, arguments):
    print(f"\n[Authorization required] {name}")
    print(json.dumps(arguments, indent=2, ensure_ascii=False))
    print("This action can change a website or execute a local command.")
    try:
        return input("Allow this action? [y/N]: ").strip().lower() == 'y'
    except (EOFError, KeyboardInterrupt):
        return False


# Optional Windows SAPI TTS Engine
def speak_text(text: str):
    """Speaks text asynchronously using Windows SAPI Speech Synthesis (TTS)."""
    if not text or not text.strip():
        return
    def _speak():
        try:
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            cleaned = re.sub(r"```[\s\S]*?```", " [code block] ", text)
            cleaned = re.sub(r"`[^`]*`", "", cleaned)
            cleaned = re.sub(r"https?://\S+", "link", cleaned)
            cleaned = re.sub(r"[#*_~>\[\]\(\)\{\}|\\-]", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                speaker.Speak(cleaned[:400])
        except Exception:
            pass
    threading.Thread(target=_speak, daemon=True).start()


def print_banner(voice_active: bool = False):
    print("=" * 68)
    print("         [RIVA-AGI] Universal Orchestration & Tool Testbed")
    print("=" * 68)
    print("  Type any natural language prompt to test agents and tool calling.")
    print("  Diagnostic Commands:")
    print("    'status'       - View configuration status and active Gemini models")
    print("    'agents'       - List all registered agents & capabilities")
    print("    'tools'        - List all registered tools & schemas")
    print("    'tools test'   - Run offline regression tests (no API key needed)")
    print("    'browser test' - Test a real browser on local fixture pages")
    print('    tool <name> {"argument": "value"} - Call any registered tool')
    print("    'browser close' - Close this chat's browser session")
    print("    'voice'        - Toggle Windows TTS voice readout (ON/OFF)")
    print("    'clear'        - Clear chat history and memory")
    print("    'exit'/'quit'  - Exit CLI")
    print(f"  [Voice TTS: {'ON' if voice_active else 'OFF'}]")
    print("=" * 68)


def show_registered_agents():
    agents = registry.get_all_capabilities()
    print("\n" + "=" * 68)
    print(f"[REGISTERED AGENTS] (Total: {len(agents)})")
    print("=" * 68)
    for name, cap in sorted(agents.items()):
        tools_str = f"Tools: {', '.join(cap.tools)}" if cap.tools else "No tools"
        print(f"  * [{name.upper()}] ({cap.agent_level})")
        print(f"      Description: {cap.description}")
        print(f"      {tools_str}")
    print("=" * 68 + "\n")


def show_registered_tools():
    tools = tool_registry.get_all_tools()
    print("\n" + "=" * 68)
    print(f"[TOOL REGISTRY] (Total Registered: {len(tools)})")
    print("=" * 68)
    for name, defn in sorted(tools.items()):
        print(f"  * {name:<18} [{defn.category}] - {defn.description}")
        if defn.parameters_schema:
            param_list = []
            for p_name, p_info in defn.parameters_schema.items():
                req = "required" if p_info.get("required") else f"default={p_info.get('default')}"
                param_list.append(f"{p_name}: {p_info.get('type')} ({req})")
            print(f"      Params: {', '.join(param_list)}")
    print("=" * 68 + "\n")


def check_system_status():
    keys = key_manager.get_all_available_keys()
    print("\n" + "=" * 68)
    print("[SYSTEM & API KEY STATUS]")
    print("=" * 68)
    print(f"  * Total API Keys in Pool: {len(keys)}")
    print("  * Key values are never displayed. Quotas are project-scoped.")
    try:
        import orchestration.orchestrator.llm as llm_engine
        print(f"  * Text Model:      {llm_engine.resolve_text_model()}")
        print(f"  * Fallback Models: {', '.join(llm_engine.FALLBACK_MODELS)}")
    except Exception:
        pass
    print("=" * 68 + "\n")


def on_progress_tracker(node_name: str, node_update: dict, current_state: dict, start_time: list, step_timer: list):
    """Prints clean timeline updates for each agent/node in the LangGraph execution."""
    elapsed = time.time() - start_time[0]
    step_duration = time.time() - step_timer[0]
    step_timer[0] = time.time()

    if node_name == "intent":
        complexity = current_state.get("complexity", "unknown").upper()
        target = current_state.get("routing_decision", "unknown").upper()
        intent_desc = current_state.get("intent", "unspecified")
        print(f"  [{elapsed:4.1f}s] [INTENT CLASSIFIER] Complexity: {complexity} | Intent: {intent_desc}")
        print(f"  [{elapsed:4.1f}s] [ROUTING] Target: [{target}]")

    elif node_name == "planner":
        plan = current_state.get("plan", [])
        print(f"  [{elapsed:4.1f}s] [PLANNER] Created {len(plan)}-step plan (took {step_duration:4.1f}s)")
        for idx, s in enumerate(plan, 1):
            agent_str = s.get("agent", "worker").upper()
            task_str = s.get("task", "")[:60]
            print(f"        Step {idx}: [{agent_str}] {task_str}")

    elif node_name == "executor":
        step_idx = current_state.get("current_step", 0)
        completed = current_state.get("completed_steps", [])
        if completed:
            last = completed[-1]
            agent_str = last.get("agent", "worker").upper()
            print(f"  [{elapsed:4.1f}s] [EXECUTOR] Executed Step {step_idx} via [{agent_str}] (took {step_duration:4.1f}s)")

    elif node_name == "reviewer":
        decision = current_state.get("routing_decision", "approved")
        feedback = current_state.get("feedback", "")
        print(f"  [{elapsed:4.1f}s] [REVIEWER] Decision: {decision.upper()} (took {step_duration:4.1f}s)")
        if feedback:
            print(f"        Feedback: {feedback[:80]}...")

    else:
        # Worker agent execution
        resp = node_update.get("response_payload")
        tools_ran = []
        if resp and hasattr(resp, "tool_calls") and resp.tool_calls:
            for tc in resp.tool_calls:
                name = getattr(tc, "tool_name", str(tc))
                result = getattr(tc, 'result', None)
                tools_ran.append(f"{name}: {'FAILED' if result and not result.success else 'OK'}")

        status = resp.status.value.upper() if resp else 'FINISHED'
        print(f"  [{elapsed:4.1f}s] [{node_name.upper()}] {status} (took {step_duration:4.1f}s)")
        if tools_ran:
            print(f"        Tools Executed ({len(tools_ran)}):")
            for t_str in tools_ran:
                print(f"           * {t_str}")


def execute_prompt(user_input: str, conversation_history: list, session_id: str = 'cli', authorize=authorize_action, return_response=False):
    """Executes the user prompt through the LangGraph orchestrator."""
    print("\n" + "=" * 68)
    print("[LANGGRAPH EXECUTION TIMELINE]")
    print("=" * 68)

    t0 = time.time()
    start_time = [t0]
    step_timer = [t0]

    app = create_orchestrator()
    task_id = f"task-{uuid.uuid4().hex[:8]}"

    # Fast-path for standalone greetings
    if user_input.lower().strip().rstrip(".!?") in {"hello", "hi", "hey", "greetings", "good morning", "good evening", "good afternoon"}:
        effective_prompt = user_input
    elif conversation_history:
        context_lines = []
        for turn in conversation_history[-3:]:
            context_lines.append(f"User: {turn['user']}")
            context_lines.append(f"Assistant: {turn['assistant']}")
        context_block = "\n".join(context_lines)
        effective_prompt = f"Previous conversation context:\n{context_block}\n\nCurrent user request:\n{user_input}"
    else:
        effective_prompt = user_input

    final_agent = "assistant"
    final_payload = None

    current_state = initial_state(effective_prompt, task_id=task_id, session_id=session_id)
    policy = ExecutionPolicy(session_id=session_id, authorize=authorize)
    try:
        with execution_scope(policy):
            for step in app.stream(current_state, config={'recursion_limit': 40}):
                for node_name, node_update in step.items():
                    if isinstance(node_update, dict):
                        current_state.update(node_update)
                        if node_update.get('response_payload'):
                            final_payload = node_update['response_payload']
                            final_agent = final_payload.agent_id
                        on_progress_tracker(node_name, node_update, current_state, start_time, step_timer)
    except KeyboardInterrupt:
        policy.cancelled.set()
        raise

    total_duration = time.time() - t0
    print("=" * 68)
    print(f"[FINISHED] in {total_duration:.2f}s | Final Agent: [{final_agent.upper()}]")
    print("=" * 68)

    print("\n[FINAL AGENT OUTPUT]:")
    output_text = ""
    if not output_text:
        if final_payload and hasattr(final_payload, "content") and final_payload.content:
            output_text = final_payload.content
        elif isinstance(final_payload, dict):
            output_text = final_payload.get("content", str(final_payload))
        elif current_state.get("response_payload") and hasattr(current_state["response_payload"], "content"):
            output_text = current_state["response_payload"].content
        else:
            output_text = "Task ended without a final response."

    if final_payload:
        print(f"[STATUS: {final_payload.status.value.upper()}]")
        for call in final_payload.tool_calls:
            if call.result:
                label = 'OK' if call.result.success else 'FAILED'
                print(f"  [{label}] {call.tool_name} ({call.result.execution_time_ms:.0f} ms)")
                if call.result.error:
                    print(f"    {call.result.error}")

    print(output_text)
    print("-" * 68)
    return final_payload if return_response else output_text


def run_tools_self_test(browser=False):
    if browser and importlib.util.find_spec('playwright') is None:
        print('Browser tests require requirements-browser.txt and Chromium. See orchestration/README.md.')
        return 2
    tests = ['tests/integration/test_browser_workflow.py'] if browser else ['tests/unit', 'tests/integration/test_orchestrator.py']
    return subprocess.run([sys.executable, '-m', 'pytest', '-q', *tests], cwd=WORKSPACE_ROOT).returncode


def execute_direct_tool(command, session_id):
    parts = command.strip().split(' ', 1)
    name = parts[0]
    raw = parts[1].strip() if len(parts) > 1 else '{}'
    if raw.startswith('{'):
        arguments = json.loads(raw)
    elif name in {'read_file', 'list_directory', 'execute_command'}:
        parameter = {'read_file': 'file_path', 'list_directory': 'dir_path', 'execute_command': 'command'}[name]
        arguments = {parameter: raw}
    else:
        raise ValueError('Supply tool arguments as a JSON object. Use tools to inspect parameters.')
    if not isinstance(arguments, dict):
        raise ValueError('Arguments must be a JSON object.')
    with execution_scope(ExecutionPolicy(session_id=session_id, authorize=authorize_action)):
        result = tool_registry.execute_result(name, **arguments)
    print(result.model_dump_json(indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description='RIVA interactive agent and browser test console')
    parser.add_argument('--self-test', action='store_true', help='Run offline tests without LLM requests')
    parser.add_argument('--browser-test', action='store_true', help='Run Chromium tests on local fixture pages')
    parser.add_argument('--prompt', help='Run a single prompt, then exit')
    args = parser.parse_args()
    if args.self_test or args.browser_test:
        return run_tools_self_test(browser=args.browser_test)
    if args.prompt:
        response = execute_prompt(args.prompt, [], session_id=uuid.uuid4().hex, return_response=True)
        return 0 if response and response.status == ResponseStatus.SUCCESS else 1
    session_id = uuid.uuid4().hex
    voice_mode = False
    conversation_history = []
    print_banner(voice_active=voice_mode)

    while True:
        try:
            user_input = input("\n> You: ").strip()
            if not user_input:
                continue

            # Core Command Handling
            cmd_lower = user_input.lower()
            if cmd_lower in ["exit", "quit", "q"]:
                print("Exiting Riva-AGI. Goodbye!")
                break

            if cmd_lower in ["clear", "reset"]:
                conversation_history.clear()
                with execution_scope(ExecutionPolicy(session_id=session_id)):
                    browser_service.call('close')
                print("[*] Conversation history cleared.")
                continue

            if cmd_lower in ["status", "pool"]:
                check_system_status()
                continue

            if cmd_lower in ["agents", "models"]:
                show_registered_agents()
                continue

            if cmd_lower == "tools":
                show_registered_tools()
                continue

            if cmd_lower in ["tools test", "tool test", "test tools"]:
                run_tools_self_test()
                continue

            if cmd_lower == 'browser test':
                run_tools_self_test(browser=True)
                continue
            if cmd_lower == 'browser close':
                execute_direct_tool('browser_close {}', session_id)
                continue

            if cmd_lower in ["voice", "tts"]:
                voice_mode = not voice_mode
                status_str = "ENABLED" if voice_mode else "DISABLED"
                print(f"[Voice TTS: {status_str}]")
                if voice_mode:
                    speak_text("Voice mode activated.")
                continue

            # Direct tool invocation: tool <tool_name> <params...>
            if user_input.startswith("tool "):
                execute_direct_tool(user_input[5:], session_id)
                continue
            # Execute via LangGraph Orchestrator
            output_text = execute_prompt(user_input, conversation_history, session_id=session_id)

            if voice_mode and output_text:
                speak_text(output_text)

            if output_text:
                conversation_history.append({
                    "user": user_input,
                    "assistant": output_text[:1200]
                })

        except (KeyboardInterrupt, EOFError):
            print("\nSession interrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\n[!] Execution Error: {e}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        browser_service.shutdown()
