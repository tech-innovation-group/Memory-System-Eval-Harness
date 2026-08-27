FROM python:3.12-slim

WORKDIR /app
COPY stress/echomem /app/stress/echomem
COPY stress/README.md /app/stress/README.md

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "/app/stress/echomem/runner.py"]
