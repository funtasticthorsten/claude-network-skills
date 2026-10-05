FROM python:3.13-slim

# Non-root runtime user
RUN useradd --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jobsearch_gateway/ /app/jobsearch_gateway/

USER app

EXPOSE 8080

CMD ["uvicorn", "jobsearch_gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]