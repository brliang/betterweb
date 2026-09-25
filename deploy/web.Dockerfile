# syntax=docker/dockerfile:1
# The frontend's static build, served by Caddy (deploy/Caddyfile). Built from the repo root.
FROM node:24.21.0-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

FROM caddy:2.11.4-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
