from fastapi import FastAPI
from pydantic import BaseModel

api = FastAPI()


class Query(BaseModel):
    text: str
    k: int = 5


@api.post("/match")
def match(q: Query):
    return ranker.top_k(q.text, q.k)
