FROM python:3.11-slim

# System dependencies required by Playwright/Chromium on Linux
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libcairo2 libdbus-1-3 \
    fonts-liberation libappindicator3-1 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium
RUN playwright install-deps chromium

COPY . .

# Create data directories expected by the runners
RUN mkdir -p data/mlb

# Default: run the NBA daily runner.
# Override in Railway to run mlb_runner.py for the MLB service.
CMD ["python", "daily_runner.py"]
