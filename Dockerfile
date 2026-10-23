FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/var/lib/onetime-file PORT=3000
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app && mkdir -p /var/lib/onetime-file && chown app:app /var/lib/onetime-file
COPY --chown=app:app app.py ./
COPY --chown=app:app static/ ./static/
USER app
EXPOSE 3000
CMD ["sh", "-c", "exec gunicorn 'app:create_app()' --bind 0.0.0.0:${PORT:-3000} --workers 1 --threads 4 --error-logfile -"]
