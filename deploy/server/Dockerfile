FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
ENV QWENPAW_SERVER_MODE=1
WORKDIR /opt/qwenpaw
COPY . .
RUN pip install --no-cache-dir '.[server]' && python -m playwright install --with-deps chromium && useradd --uid 1000 --create-home qwenpaw
USER 1000:1000
ENTRYPOINT ["qwenpaw", "serve"]
CMD ["api"]
