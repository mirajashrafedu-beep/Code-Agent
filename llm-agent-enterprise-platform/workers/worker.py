import os
from celery import Celery

redis_uri = os.getenv("REDIS_URI", "redis://redis:6379/0")

celery_app = Celery(
    "enterprise_workers",
    broker=redis_uri,
    backend=redis_uri,
    include=[
        "tasks.vector_indexing",
        "tasks.thread_summarizer",
        "tasks.telemetry_flush"
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
)

if __name__ == "__main__":
    celery_app.start()
