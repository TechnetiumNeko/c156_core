FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run typecheck && npm run build
ARG BUILD_SHA=dev
RUN node -e 'require("fs").writeFileSync("dist/build-info.json", JSON.stringify({build_sha:process.argv[1]}))' "$BUILD_SHA"
FROM nginx:stable-alpine@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94
ARG BUILD_SHA=dev
LABEL org.opencontainers.image.revision=${BUILD_SHA}
COPY deploy/frontend-nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
