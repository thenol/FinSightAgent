FROM node:22-alpine AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
COPY --from=web-builder /web/dist ./web/dist
COPY deploy/entrypoint.sh /entrypoint.sh
RUN pip install --no-cache-dir ".[postgres]" \
    && groupadd --system finsight \
    && useradd --system --gid finsight --home-dir /app finsight \
    && mkdir -p /data/artifacts /data/market \
    && chown -R finsight:finsight /app /data /entrypoint.sh \
    && chmod 0555 /entrypoint.sh

USER finsight

EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["/entrypoint.sh"]
