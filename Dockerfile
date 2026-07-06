FROM python:3.12-slim

WORKDIR /

COPY main.py .

CMD ["python", "main.py"]
