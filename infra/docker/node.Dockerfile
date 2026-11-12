FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY package*.json ./
COPY apps/api/package.json apps/api/package.json
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/shared/package.json packages/shared/package.json
RUN npm ci
COPY apps apps
COPY packages packages
ARG APP
RUN npm run build -w @bsb/${APP}
RUN npm prune --omit=dev --ignore-scripts --no-audit --no-fund
FROM node:22-bookworm-slim AS runtime
WORKDIR /app
COPY --from=build --chown=node:node /app /app
USER node
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
CMD ["npm", "run", "start", "-w", "@bsb/api"]
