from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, RootModel


class IntentDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    complexity: Literal['simple', 'complex']
    target_agent: str
    intent: str
    confidence: float = Field(ge=0, le=1)


class PlanStep(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agent: str
    task: str = Field(min_length=1)


class ExecutionPlan(RootModel[list[PlanStep]]):
    pass


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['approved', 'rejected']
    feedback: str
