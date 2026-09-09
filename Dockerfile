FROM node:22.20.0-bookworm-slim@sha256:b21fe589dfbe5cc39365d0544b9be3f1f33f55f3c86c87a76ff65a02f8f5848e AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM golang:1.25.1-bookworm@sha256:c423747fbd96fd8f0b1102d947f51f9b266060217478e5f9bf86f145969562ee AS age_builder
ARG AGE_REVISION=b74dce4cdbe35b5e5f66c06d9612b72f89028758
ARG AGE_MODULE_SUM=h1:r6RSZLFSMm6rzKepZ7ZAYkKCu14f3/Me8c7uKYh7C8c=
RUN set -eu; \
    test "${AGE_REVISION}" = "b74dce4cdbe35b5e5f66c06d9612b72f89028758"; \
    test "${AGE_MODULE_SUM}" = "h1:r6RSZLFSMm6rzKepZ7ZAYkKCu14f3/Me8c7uKYh7C8c="; \
    mkdir /out; \
    GOSUMDB=sum.golang.org go mod download -json filippo.io/age@${AGE_REVISION} > /tmp/age-module.json; \
    grep -F "${AGE_MODULE_SUM}" /tmp/age-module.json >/dev/null; \
    GOSUMDB=sum.golang.org GOBIN=/out CGO_ENABLED=0 go install filippo.io/age/cmd/age@${AGE_REVISION}; \
    GOSUMDB=sum.golang.org GOBIN=/out CGO_ENABLED=0 go install filippo.io/age/cmd/age-keygen@${AGE_REVISION}; \
    rm /tmp/age-module.json

FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/usr/local/bin:/usr/bin:/bin
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r /app/requirements.lock
COPY workspace/ /app/workspace/
COPY contracts/ /app/contracts/
COPY --from=frontend /build/frontend/dist/ /app/frontend/dist/
COPY --from=age_builder /out/age /out/age-keygen /usr/local/bin/
COPY licenses/age-BSD-3-Clause.txt /usr/share/doc/age/copyright
RUN chmod -R a=rX /app /usr/local/bin/age /usr/local/bin/age-keygen /usr/share/doc/age/copyright
USER 65532:65532
CMD ["python", "-m", "workspace.cli", "--help"]
