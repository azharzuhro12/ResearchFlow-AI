# ResearchFlow AI — frontend image (Next.js 16, plain `next start`).
#
# The only build-time variable is NEXT_PUBLIC_API_BASE_URL — a PUBLIC value
# by design (it names the backend the browser talks to). Backend secrets
# never enter this build: they live in backend/.env and are passed to the
# backend service at runtime only.

FROM node:22-slim

WORKDIR /app

# Dependencies first for layer caching (package-lock.json is committed).
COPY package.json package-lock.json ./
RUN npm ci

# Build with the public API base URL baked in. Default matches the
# docker-compose port mapping (host 8010 → backend container 8000).
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8010
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build

EXPOSE 3000

CMD ["npm", "start"]
