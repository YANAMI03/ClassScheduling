# ==============================================================================
# ClassScheduling Dockerfile
# Multi-platform development and production-ready Python container
# ==============================================================================

FROM python:3.12-slim

# Prevent Python from writing .pyc files to disk and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=5000

# Set working directory inside the container
WORKDIR /app

# Install minimal system dependencies (curl for healthchecks)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first to leverage Docker layer caching
COPY requirements.txt /app/

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy the application source code
COPY . /app/

# Expose the application port
EXPOSE 5000

# Health check to ensure the application is responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/ || exit 1

# Default command to start the Flask application
CMD ["python", "app.py"]
