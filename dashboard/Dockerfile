# syntax=docker/dockerfile:1
# Build from local-ecosystem repository root:
#   docker build -t local/service-dashboard:latest -f dashboard/Dockerfile .
# Repo-root `.dockerignore` trims the build context (see ecosystem-stack/services/dashboard.sh).

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_PORT=8090
ENV DASHBOARD_PROJECT_ROOT=/project
ENV LECO_ECOSYSTEM_ROOT=/project
# Slow or flaky networks during `docker build` often hit pip’s default ~15s read timeout.
ENV PIP_DEFAULT_TIMEOUT=180
ENV DEBIAN_FRONTEND=noninteractive
ENV APT_LISTCHANGES_FRONTEND=none

# Keep apt separate from curl/tar: one giant RUN often looks “stuck” on libc triggers while the
# shell is actually waiting on the next network download (no log lines).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Persist tarball/binary across builds (BuildKit). Without this, every `dashboard.sh deploy` re-downloads
# from docker.com + GitHub (~minutes on slow links).
RUN --mount=type=cache,target=/dl,sharing=locked \
    ARCH=$(dpkg --print-architecture) \
  && case "$ARCH" in amd64) DC=x86_64;; arm64) DC=aarch64;; *) DC=$ARCH;; esac \
  && TGZ="/dl/docker-27.4.1-${DC}.tgz" \
  && DCP="/dl/docker-compose-2.32.4-${DC}" \
  && if [ ! -s "$TGZ" ]; then \
       echo "Downloading docker static (${DC})…" \
       && curl -fsSL --retry 5 --retry-delay 2 --connect-timeout 30 \
            "https://download.docker.com/linux/static/stable/${DC}/docker-27.4.1.tgz" \
            -o "$TGZ.part" && mv "$TGZ.part" "$TGZ"; \
     fi \
  && tar xzf "$TGZ" -C /tmp \
  && mv /tmp/docker/docker /usr/local/bin/ \
  && mkdir -p /usr/local/lib/docker/cli-plugins \
  && if [ ! -s "$DCP" ]; then \
       echo "Downloading compose plugin (${DC})…" \
       && curl -fsSL --retry 5 --retry-delay 2 --connect-timeout 30 \
            "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-${DC}" \
            -o "$DCP.part" && mv "$DCP.part" "$DCP"; \
     fi \
  && cp "$DCP" /usr/local/lib/docker/cli-plugins/docker-compose \
  && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
  && rm -rf /tmp/docker

COPY tools/deploy-cli /app/tools/deploy-cli

COPY dashboard/requirements.txt .
RUN pip install --no-cache-dir --retries 5 -r requirements.txt \
  && pip install --no-cache-dir --retries 5 -e /app/tools/deploy-cli

COPY dashboard/app.py .
COPY dashboard/monitor.py .
COPY dashboard/service_hub.py .
COPY dashboard/reference_data.py .
COPY dashboard/docs_catalog.py .
COPY dashboard/host_metrics.py .
COPY dashboard/timeseries.py .
COPY dashboard/control_targets.py .
COPY dashboard/leco_control.py .
COPY dashboard/leco_detect.py .
COPY dashboard/leco_runtimes ./leco_runtimes
COPY dashboard/leco_validate.py .
COPY dashboard/hosting_layout.py .
COPY dashboard/hosted_zip_upload.py .
COPY dashboard/leco_registration.py .
COPY dashboard/leco_subprocess.py .
COPY dashboard/hosted_apps.py .
COPY dashboard/hosted_app_services.py .
COPY dashboard/hosted_data_import.py .
COPY dashboard/port_utils.py .
COPY dashboard/hosted_app_timeseries.py .
COPY dashboard/help_manual.py .
COPY dashboard/traefik_manifest_keys.py .
COPY dashboard/traefik_dynamic_file.py .
COPY dashboard/local_cf_teardown.py .
COPY dashboard/hosted_offboard.py .
COPY dashboard/control.py .
COPY dashboard/service_policies.py .
COPY dashboard/ui_credentials.py .
COPY dashboard/ui_credential_reset.py .
COPY dashboard/ui_login_assist.py .
COPY dashboard/ui_provision.py .
COPY ecosystem-stack/config/ui-login-registry.json /app/ecosystem-stack/config/ui-login-registry.json
COPY dashboard/ollama_models.py .
COPY dashboard/templates ./templates
COPY dashboard/static ./static

EXPOSE 8090

# dashboard.sh mounts the repo at /project. Run from /project/dashboard so Python + templates + static
# match the host without rebuilding the image (image /app is fallback when /project is not mounted).
CMD ["sh", "-c", "if [ -f /project/dashboard/app.py ]; then cd /project/dashboard && exec python app.py; else cd /app && exec python app.py; fi"]
