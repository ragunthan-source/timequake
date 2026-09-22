FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY services/ services/

# Copy project files
COPY chain_config.json .
COPY tracer.py .
COPY rca_engine.py .
COPY rollback_engine.py .
COPY api.py .

# Expose the service port (5000 + index in chain)
# We'll use environment variable for the service name
ENV SERVICE_NAME=auth

# Default command - will be overridden by docker-compose
CMD ["python", "services/service_node.py", "$SERVICE_NAME"]