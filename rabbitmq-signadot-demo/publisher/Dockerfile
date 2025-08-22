FROM python:3.9-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && opentelemetry-bootstrap -a install

COPY app.py .

CMD ["opentelemetry-instrument","--traces_exporter","console","--metrics_exporter","none","--service_name","publisher","python","app.py"]