FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json .
RUN npm install
COPY . .
ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx-frontend.conf /etc/nginx/conf.d/default.conf.template
EXPOSE 80
CMD ["/bin/sh", "-c", "cp /etc/nginx/conf.d/default.conf.template /etc/nginx/conf.d/default.conf && sed -i \"s|RAILWAY_PORT|${PORT:-80}|g\" /etc/nginx/conf.d/default.conf && sed -i \"s|BACKEND_SERVICE_URL|${BACKEND_URL}|g\" /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
