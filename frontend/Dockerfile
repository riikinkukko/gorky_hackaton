# Сборка
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# По умолчанию — настоящий API. Для стенда без бэкенда: --build-arg VITE_USE_MOCK=1
ARG VITE_USE_MOCK=0
ARG VITE_FREEZE_NOW=
ENV VITE_USE_MOCK=$VITE_USE_MOCK VITE_FREEZE_NOW=$VITE_FREEZE_NOW
RUN npm run build

# Раздача: nginx на :80, Caddy проксирует сюда всё, кроме /api, /ws, /calendar, /health
FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
