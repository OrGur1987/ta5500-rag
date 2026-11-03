FROM python:3.12-slim

WORKDIR /app
COPY service/requirements.txt service/requirements.txt
RUN pip install --no-cache-dir -r service/requirements.txt

COPY models.yml .
COPY rag rag
COPY service service

# index bundle (chunks, nodes, embeddings) is mounted here
ENV RAG_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8000"]
