FROM node:24-bookworm-slim AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY tsconfig*.json eslint.config.js ./
COPY src ./src
RUN npm run build

FROM node:24-bookworm-slim AS browser
RUN apt-get update && apt-get install -y --no-install-recommends chromium ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

FROM browser AS test
COPY --from=dependencies /app /app
COPY tests ./tests
USER node
CMD ["npm", "run", "test:browser"]

FROM dependencies AS production-dependencies
RUN npm prune --omit=dev --ignore-scripts

FROM browser AS runtime
COPY --from=production-dependencies /app/node_modules ./node_modules
COPY --from=dependencies /app/dist ./dist
COPY package.json ./
USER node
CMD ["node", "dist/main.js"]
