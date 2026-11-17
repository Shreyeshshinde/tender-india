FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# --only-shell: the small headless Chromium. Servers never show a browser window
# (the CAPTCHA helpers that do are run on your own PC).
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/* /root/.cache
COPY . .
EXPOSE 8000
# Same image for the website API (this command) and the scraper job
# (Cloud Run Job runs: python -m app.schedule --once). Cloud Run sets $PORT.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
