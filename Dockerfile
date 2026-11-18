FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 helper \
    && useradd --uid 10001 --gid helper --no-create-home helper \
    && mkdir /data && chown helper:helper /data
COPY --chown=helper:helper helper ./helper
COPY --chown=helper:helper docs/upstream-baseline.json ./docs/upstream-baseline.json
COPY --chown=helper:helper ui ./ui
COPY --chown=helper:helper LICENSES ./LICENSES
COPY --chown=helper:helper THIRD_PARTY_NOTICES.md ./
USER 10001:10001
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/_helper/healthz', timeout=3)"
CMD ["python", "-m", "helper"]
