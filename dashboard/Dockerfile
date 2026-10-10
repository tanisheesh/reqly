FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Build-time defaults. A published image is configured at run time instead,
# with REQLY_COLLECTOR_URL / REQLY_READ_KEY (see docker/entrypoint.mjs).
ARG VITE_COLLECTOR_URL=http://localhost:8000
ARG VITE_READ_KEY=demo-read-key
ENV VITE_COLLECTOR_URL=$VITE_COLLECTOR_URL
ENV VITE_READ_KEY=$VITE_READ_KEY
RUN npm run build

FROM node:22-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
COPY docker/entrypoint.mjs ./entrypoint.mjs
# The entrypoint rewrites dist/config.js at start-up, as the unprivileged user.
RUN chown -R node:node /app/dist
USER node
EXPOSE 5173
CMD ["node", "entrypoint.mjs"]
