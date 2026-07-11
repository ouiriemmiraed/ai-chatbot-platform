# Build stage
FROM node:18-alpine AS build

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies (including dev deps needed for the build)
RUN npm install

# Copy source code
COPY . .

# Public backend URL baked into the bundle. Without this CRA falls back to
# http://localhost:8000 (chatApi.js), which a browser on a customer site can
# never reach (loopback CORS block) — the widget then loads no config and the
# panel renders unstyled/off-screen. Override at build with:
#   docker build --build-arg REACT_APP_API_URL=https://api.example.com .
ARG REACT_APP_API_URL=http://93.127.136.248:8001
ENV REACT_APP_API_URL=${REACT_APP_API_URL}

# Build the widget and generate widget.bundle.js
RUN npm run build:widget

# Production stage
FROM nginx:alpine

# Copy built files
COPY --from=build /app/build /usr/share/nginx/html

# Copy nginx configuration
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 3001

# Health check — use curl which is available in nginx:alpine
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f -s http://localhost:3001 || exit 1

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
