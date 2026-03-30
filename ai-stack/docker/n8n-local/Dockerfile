# Main n8n image does not ship @n8n/task-runner-python (internal runner expects
# /usr/local/lib/node_modules/@n8n/task-runner-python/.venv/bin/python — see
# n8n/dist/task-runners/task-runner-process-py.js). Copy the official runners
# payload + Python 3.13 from n8nio/runners (task-runner-python requires >=3.13).
#
# Keep the runners tag aligned with your n8n tag (both :latest or same version).
ARG RUNNERS_IMAGE=n8nio/runners:latest
ARG N8N_IMAGE=docker.n8n.io/n8nio/n8n:latest

FROM ${RUNNERS_IMAGE} AS runners

FROM ${N8N_IMAGE}

USER root

# Python 3.13 (Alpine musl) — matches runners image
COPY --from=runners /usr/local/bin/python3.13 /usr/local/bin/python3.13
COPY --from=runners /usr/local/lib/libpython3.13.so.1.0 /usr/local/lib/
COPY --from=runners /usr/local/lib/libpython3.13.so /usr/local/lib/
COPY --from=runners /usr/local/lib/libpython3.so /usr/local/lib/
COPY --from=runners /usr/local/lib/python3.13 /usr/local/lib/python3.13

RUN ln -sf python3.13 /usr/local/bin/python3 \
  && ln -sf python3 /usr/local/bin/python

RUN mkdir -p /usr/local/lib/node_modules/@n8n
COPY --from=runners /opt/runners/task-runner-python /usr/local/lib/node_modules/@n8n/task-runner-python

RUN chown -R node:node /usr/local/lib/node_modules/@n8n/task-runner-python

USER node
