# Build the static dashboard, then serve it with nginx.
# The API address is baked in at build time (it is a browser-side setting):
#   docker build --build-arg VITE_API_URL=https://api.example.com .
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL=http://localhost:8000
ARG VITE_WEBHOOK_INBOX_URL=
ENV VITE_API_URL=$VITE_API_URL VITE_WEBHOOK_INBOX_URL=$VITE_WEBHOOK_INBOX_URL
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
