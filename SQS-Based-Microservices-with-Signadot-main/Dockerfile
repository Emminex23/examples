# Use an official Python runtime as a parent image.
# Using -slim provides a smaller base image.
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies.
# This is done in a separate step to leverage Docker's layer caching.
# The dependency installation layer will only be rebuilt if requirements.txt changes.
COPY requirements.txt .

# Upgrade pip and install packages
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install OpenTelemetry SDK + instrumentations
RUN pip install --no-cache-dir \
    opentelemetry-distro \
    opentelemetry-exporter-otlp \
    opentelemetry-instrumentation-asgi \
    opentelemetry-instrumentation-fastapi \
    opentelemetry-instrumentation-requests \
    opentelemetry-instrumentation-botocore

# Install OpenTelemetry bootstrap separately
RUN opentelemetry-bootstrap -a install

# Copy the rest of the application's code into the container
COPY . .

# Command to run your application. Replace `main.py` with your application's entry point.
CMD ["python", "main.py"]