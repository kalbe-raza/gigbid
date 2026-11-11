# helpdesk-rag

Retrieval-augmented support assistant. Built 2024. (Synthetic portfolio repository.)

## Architecture
Chunked the docs by heading, embedded them into a vector index, cached frequent answers and forced the assistant to cite the article it used.

## Tech
- LangChain
- OpenAI API
- Retrieval-Augmented Generation
- Python
- FastAPI
- Pinecone
- Redis

## Layout
- `main.py`
- `index_store.py`
- `cache.py`
- `api.py`

## Running
```bash
cp .env.example .env
make run
```
