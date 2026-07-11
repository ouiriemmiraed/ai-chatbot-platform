FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including Playwright/Chromium requirements
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (LTS version) for widget building
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .

# Upgrade pip and install with increased timeout and retries
# Using --no-cache-dir to reduce disk usage during build
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --default-timeout=300 --retries 5 -r requirements.txt

# Set Playwright environment to store browsers in /ms-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Playwright browsers (as root before switching user)
RUN playwright install chromium --with-deps

# Copy application code
COPY . .

# Create uploads directory
RUN mkdir -p uploads

# Create non-root user for security and set proper permissions
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
RUN chown -R appuser:appgroup /app
RUN chown -R appuser:appgroup /ms-playwright
USER appuser

# Expose port
EXPOSE 8000

# Health check — start_period=120s to allow embedding models to load on first start
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')" || exit 1

# Run with uvicorn (optimized for memory usage)
# Using 1 worker: FastAPI is async so 1 process handles many concurrent requests.
# With --workers 2 the embedding model (~800MB) was loaded TWICE → 1.6GB wasted.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "50", "--timeout-keep-alive", "30"]
