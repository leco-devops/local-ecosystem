FROM node:22-bookworm-slim

WORKDIR /app

COPY package.json ./
RUN npm install --omit=dev

COPY worker.js ./

ENV NODE_ENV=production
EXPOSE 8787

CMD ["npx", "miniflare", "worker.js", "--port", "8787", "--host", "0.0.0.0"]
