# callnotes

Call-centre transcription and summarisation. Built 2024. (Synthetic portfolio repository.)

## Architecture
Transcribed calls with Whisper in a background queue and produced structured summaries with an LLM.

## Tech
- Speech Recognition
- Whisper
- Large Language Models
- Python
- Celery
- Docker

## Layout
- `main.py`
- `tasks.py`
- `Dockerfile`

## Running
```bash
cp .env.example .env
make run
```
