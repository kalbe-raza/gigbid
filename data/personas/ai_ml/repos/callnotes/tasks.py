from celery import Celery

app = Celery("worker", broker="redis://redis:6379/0")


@app.task(bind=True, max_retries=5, default_retry_delay=30)
def process(self, item_id: str) -> None:
    try:
        handle(item_id)
    except TransientError as exc:
        raise self.retry(exc=exc)
