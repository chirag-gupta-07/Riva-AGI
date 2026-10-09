"""Sequential, bounded task orchestration with explicit step handoffs."""
import json
import uuid
from typing import TypedDict
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END

load_dotenv()

from orchestration import InputData, InputType, AgentResponse, ResponseStatus
from orchestration.orchestrator.registry import registry
from orchestration.orchestrator.state_manager import TaskStateManager, TaskStatus
from orchestration.orchestrator.router import classify_intent
from orchestration.orchestrator.schemas.planning import IntentDecision, ExecutionPlan, ReviewDecision
from orchestration.tools.policy import execution_scope, check_budget
import orchestration.orchestrator.intent_classifier
import orchestration.orchestrator.planner
import orchestration.orchestrator.reviewer
import orchestration.agents.coder
import orchestration.agents.researcher
import orchestration.agents.writer
import orchestration.agents.reasoner
import orchestration.agents.designer
import orchestration.agents.qa_tester
import orchestration.agents.data_analyst
import orchestration.agents.devops
import orchestration.agents.security_auditor
import orchestration.agents.seo_specialist
import orchestration.agents.browser

task_manager = TaskStateManager()
MAX_PLAN_STEPS = 8
MAX_REPAIRS = 1


class AgentState(TypedDict):
    task_payload: InputData
    agent: str
    response_payload: AgentResponse | None
    task_id: str
    session_id: str
    source: str
    complexity: str
    routing_decision: str
    plan: list[dict]
    current_step: int
    completed_steps: list[dict]
    feedback: str
    intent: str
    confidence: float
    review_attempts: int
    error: str


def initial_state(task_text, task_id=None, session_id='session-001', source='cli'):
    return dict(task_payload=InputData(input_type=InputType.TEXT, text_content=task_text,
                                      metadata={'source': source, 'session_id': session_id}),
                agent='fallback', response_payload=None, task_id=task_id or uuid.uuid4().hex,
                session_id=session_id, source=source, complexity='simple', routing_decision='fallback',
                plan=[], current_step=0, completed_steps=[], feedback='', intent='unknown',
                confidence=0.0, review_attempts=0, error='')


def workers():
    return {name for name, cap in registry.get_all_capabilities().items() if cap.agent_level == 'TASK_DOER'}


def clean_json(text):
    text = (text or '').strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]
    return text.strip()


def invoke_agent(name, payload, state):
    check_budget()
    handler = registry.get_agent(name)
    if handler is None:
        raise ValueError(f'Unknown agent: {name}')
    with execution_scope(session_id=state['session_id']):
        response = handler(payload)
    if response.status != ResponseStatus.SUCCESS:
        error = RuntimeError(response.error_message or response.content or 'Agent failed')
        error.response = response
        raise error
    return response


def failure(state, message, response=None):
    task_manager.update_task_state(state['task_id'], 'orchestrator', TaskStatus.FAILED)
    return {'error': message, 'routing_decision': 'failed', 'response_payload': response or
            AgentResponse(agent_id='orchestrator', status=ResponseStatus.FAILURE,
                          content=message, error_message=message)}


def intent_node(state):
    task_manager.start_task(state['task_id'], {'task': state['task_payload'].text_content})
    try:
        response = invoke_agent('intent_classifier', state['task_payload'], state)
        decision = IntentDecision.model_validate_json(clean_json(response.content))
        target = 'planner' if decision.complexity == 'complex' else decision.target_agent
        if target not in workers() | {'planner', 'fallback'}:
            target = 'fallback'
        return dict(complexity='complex' if target == 'planner' else 'simple',
                    routing_decision=target, agent=target, intent=decision.intent, confidence=decision.confidence)
    except Exception:
        choice = classify_intent(state['task_payload'].text_content or '')
        target = choice['agent']
        return dict(complexity='complex' if target == 'planner' else 'simple',
                    routing_decision=target, agent=target, intent=choice['intent'], confidence=choice['confidence'])


def planner_node(state):
    try:
        response = invoke_agent('planner', state['task_payload'], state)
        plan = ExecutionPlan.model_validate_json(clean_json(response.content)).model_dump()
        if not 1 <= len(plan) <= MAX_PLAN_STEPS or any(s['agent'] not in workers() for s in plan):
            raise ValueError('Plan must contain 1–8 steps assigned to registered workers.')
        return dict(plan=plan, current_step=0, completed_steps=[])
    except Exception as exc:
        return failure(state, f'Planning failed: {exc}', getattr(exc, 'response', None))


def executor_node(state):
    if state.get('error'):
        return {'routing_decision': 'failed'}
    index = state['current_step']
    target = state['plan'][index]['agent'] if index < len(state['plan']) else 'reviewer'
    return {'routing_decision': target, 'agent': target}


def create_agent_node(agent_name):
    def run(state):
        task_manager.update_task_state(state['task_id'], agent_name, TaskStatus.PROCESSING)
        payload = state['task_payload']
        if state['complexity'] == 'complex':
            step = state['plan'][state['current_step']]
            context = {'original_goal': payload.text_content, 'assigned_step': step['task'],
                       'previous_results': state['completed_steps'], 'review_feedback': state['feedback']}
            payload = payload.model_copy(update={'text_content': json.dumps(context, ensure_ascii=False)})
        try:
            response = invoke_agent(agent_name, payload, state)
            completed = state['completed_steps'] + [{'agent': agent_name, 'result': response.content,
                'tool_calls': [call.model_dump() for call in response.tool_calls]}]
            task_manager.update_task_state(state['task_id'], agent_name,
                TaskStatus.PROCESSING if state['complexity'] == 'complex' else TaskStatus.COMPLETED)
            return dict(response_payload=response, completed_steps=completed, current_step=state['current_step'] + 1)
        except Exception as exc:
            return failure(state, f'{agent_name} failed: {exc}', getattr(exc, 'response', None))
    return run


def reviewer_node(state):
    payload = state['task_payload'].model_copy(update={'text_content': json.dumps({
        'original_goal': state['task_payload'].text_content, 'outputs': state['completed_steps']})})
    try:
        response = invoke_agent('reviewer', payload, state)
        review = ReviewDecision.model_validate_json(clean_json(response.content))
        if review.status == 'approved':
            task_manager.update_task_state(state['task_id'], 'orchestrator', TaskStatus.COMPLETED)
            # Keep the worker deliverable, not the review JSON.
            return {'routing_decision': 'approved', 'feedback': review.feedback}
        if state.get('review_attempts', 0) >= MAX_REPAIRS:
            return failure(state, f'Review rejected after repair: {review.feedback}')
        # Never replay external actions to repair prose. Send all evidence to a reasoning worker.
        repair = {'agent': 'reasoner', 'task': 'Repair the final answer using existing evidence. '
                  'Do not claim an unperformed action succeeded. Explain unresolved limitations. ' + review.feedback}
        return dict(plan=state['plan'] + [repair], feedback=review.feedback,
                    review_attempts=state.get('review_attempts', 0) + 1, routing_decision='rejected')
    except Exception as exc:
        return failure(state, f'Review failed: {exc}', getattr(exc, 'response', None))


def fallback_node(state):
    return failure(state, 'Fallback agent reached: no suitable worker could be selected.')


def route_after_intent(state):
    return 'planner' if state['complexity'] == 'complex' else state['routing_decision']


def route_after_executor(state):
    return END if state.get('error') else state['routing_decision']


def route_after_worker(state):
    return 'executor' if not state.get('error') and state['complexity'] == 'complex' else END


def route_after_reviewer(state):
    return 'executor' if state['routing_decision'] == 'rejected' else END


def create_orchestrator():
    from orchestration.tools import tool_registry
    for name, cap in registry.get_all_capabilities().items():
        missing = set(cap.tools) - tool_registry.get_all_tools().keys()
        if missing:
            raise ValueError(f'{name} declares unknown tools: {sorted(missing)}')
    graph = StateGraph(AgentState)
    for name, fn in [('intent', intent_node), ('planner', planner_node), ('executor', executor_node),
                     ('reviewer', reviewer_node), ('fallback', fallback_node)]:
        graph.add_node(name, fn)
    for name in workers():
        graph.add_node(name, create_agent_node(name))
    graph.add_edge(START, 'intent')
    graph.add_conditional_edges('intent', route_after_intent, {n: n for n in workers() | {'planner', 'fallback'}})
    graph.add_edge('planner', 'executor')
    graph.add_conditional_edges('executor', route_after_executor, {n: n for n in workers() | {'reviewer', END}})
    for name in workers():
        graph.add_conditional_edges(name, route_after_worker, {'executor': 'executor', END: END})
    graph.add_conditional_edges('reviewer', route_after_reviewer, {'executor': 'executor', END: END})
    graph.add_edge('fallback', END)
    return graph.compile()


def run_orchestrator(task_text, task_id=None, session_id='session-001', source='cli'):
    state = initial_state(task_text, task_id, session_id, source)
    with execution_scope(session_id=session_id):
        return create_orchestrator().invoke(state, config={'recursion_limit': 40})


if __name__ == '__main__':
    print(run_orchestrator(input('Task: '))['response_payload'].model_dump_json(indent=2))
