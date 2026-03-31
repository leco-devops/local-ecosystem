FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_PORT=8090
ENV DASHBOARD_PROJECT_ROOT=/project

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates coreutils curl \
  && ARCH=$(dpkg --print-architecture) \
  && case "$ARCH" in amd64) DC=x86_64;; arm64) DC=aarch64;; *) DC=$ARCH;; esac \
  && curl -fsSL "https://download.docker.com/linux/static/stable/${DC}/docker-27.4.1.tgz" | tar xz -C /tmp \
  && mv /tmp/docker/docker /usr/local/bin/ \
  && mkdir -p /usr/local/lib/docker/cli-plugins \
  && curl -fsSL "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-${DC}" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose \
  && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
  && rm -rf /tmp/docker /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY monitor.py .
COPY service_hub.py .
COPY reference_data.py .
COPY docs_catalog.py .
COPY host_metrics.py .
COPY timeseries.py .
COPY control_targets.py .
COPY leco_control.py .
COPY hosted_apps.py .
COPY hosted_app_timeseries.py .
COPY traefik_manifest_keys.py .
COPY traefik_dynamic_file.py .
COPY local_cf_teardown.py .
COPY hosted_offboard.py .
COPY control.py .
COPY ollama_models.py .
COPY templates ./templates
COPY static ./static

EXPOSE 8090

CMD ["python", "app.py"]
