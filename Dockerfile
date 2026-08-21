# Alternative deploy path: build a container image.
#   docker build -t creative-validation .
#   docker run -p 8080:8080 -e PORT=8080 creative-validation
FROM python:3.12-slim

# Don't buffer stdout/stderr; don't write .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code.
COPY . .

EXPOSE 8080

# Shell form so $PORT (injected by the platform) is expanded at runtime.
CMD gunicorn wsgi:app --bind 0.0.0.0:$PORT
