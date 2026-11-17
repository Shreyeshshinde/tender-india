FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Rewrites are baked in at build time: point /api/* at the backend
# (docker compose: the backend container; Cloud Run: the API service URL).
ARG API_URL=http://backend:8000
ENV API_URL=$API_URL
RUN npm run build

# Only the standalone server (~50 MB instead of ~1 GB with node_modules)
FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
