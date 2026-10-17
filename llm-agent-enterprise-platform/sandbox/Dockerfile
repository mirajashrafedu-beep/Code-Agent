FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    numpy \
    pandas \
    scipy \
    sympy \
    matplotlib \
    requests \
    pydantic \
    fastapi \
    uvicorn

COPY runner.py /app/runner.py

EXPOSE 8080

USER nobody

CMD ["uvicorn", "runner:app", "--host", "0.0.0.0", "--port", "8080"]
