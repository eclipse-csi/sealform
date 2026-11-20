# syntax=docker/dockerfile:1
# Base images are pinned by digest; refresh them together with the tags in the comments.

# uv 0.13.9
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

# python:3.13-slim
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
COPY --from=uv /uv /bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
# install exactly the locked runtime dependencies, then the package itself
RUN uv export --frozen --no-dev --no-emit-project --format requirements.txt -o /tmp/requirements.txt \
    && uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python --require-hashes -r /tmp/requirements.txt \
    && uv pip install --python /opt/venv/bin/python --no-deps .

# python:3.13-slim
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY --from=build /opt/venv /opt/venv
RUN useradd --create-home --uid 10001 signpath
ENV PATH="/opt/venv/bin:$PATH"
USER signpath
WORKDIR /config
ENTRYPOINT ["signpath-manager"]
CMD ["--help"]
