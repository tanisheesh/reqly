FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_COLLECTOR_URL=http://localhost:8000
ARG VITE_READ_KEY=demo-key
ENV VITE_COLLECTOR_URL=$VITE_COLLECTOR_URL
ENV VITE_READ_KEY=$VITE_READ_KEY
RUN npm run build

FROM node:22-alpine
WORKDIR /app
RUN npm install -g serve
COPY --from=build /app/dist ./dist
EXPOSE 5173
CMD ["serve", "-s", "dist", "-l", "5173"]
