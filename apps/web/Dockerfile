# syntax=docker/dockerfile:1.7
# tela-web (COMP-001): static SPA on unprivileged nginx. Runtime config is mounted at /config.json.
FROM node:22.12-alpine AS build
WORKDIR /web
RUN npm install -g pnpm@9.15.0 --no-fund --no-audit
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
COPY apps/web/patches ./patches
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
COPY apps/web/ ./
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
# Pull Alpine security fixes released after the base image was built (CI fails on critical CVEs).
USER root
RUN apk upgrade --no-cache
USER 101
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
