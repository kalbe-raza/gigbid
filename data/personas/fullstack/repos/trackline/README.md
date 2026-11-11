# trackline

Logistics tracking microservices. Built 2022. (Synthetic portfolio repository.)

## Architecture
Split a monolith into tracking, notification and billing services with asynchronous workers.

## Tech
- FastAPI
- Python
- Docker
- PostgreSQL
- Celery
- RabbitMQ
- Kubernetes

## Layout
- `main.py`
- `tasks.py`
- `broker.py`
- `k8s/deployment.yaml`
- `api.py`
- `Dockerfile`
- `db.py`

## Running
```bash
cp .env.example .env
make run
```
