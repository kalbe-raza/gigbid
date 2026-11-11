# lexeval

LLM evaluation harness for contract review. Built 2025. (Synthetic portfolio repository.)

## Architecture
Built a regression suite of 400 graded clause-extraction cases with structured outputs and an LLM judge calibrated against lawyer labels.

## Tech
- Large Language Models
- Prompt Engineering
- Python
- LLM Evaluation
- pytest
- Pydantic

## Layout
- `main.py`
- `tests/test_regressions.py`
- `schemas.py`

## Running
```bash
cp .env.example .env
make run
```
