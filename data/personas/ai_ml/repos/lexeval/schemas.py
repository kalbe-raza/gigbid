from pydantic import BaseModel, Field


class Clause(BaseModel):
    clause_type: str
    text: str
    confidence: float = Field(ge=0, le=1)
