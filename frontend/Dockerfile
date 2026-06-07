# Dev image for the Vite + React frontend.
# Single-stage on node:20-alpine. The dev server runs with --host 0.0.0.0
# so it is reachable from the host through the docker-compose port map.
#
# docker-compose is expected to bind-mount ./frontend into /app and to
# declare an anonymous volume on /app/node_modules so the Linux-built
# dependencies installed below are NOT shadowed by the host's
# node_modules directory.
FROM node:20-alpine

WORKDIR /app

# Install dependencies first so this layer is cached when only source
# files change.
COPY package*.json ./
RUN npm install

# Copy the rest of the frontend sources. In dev, this is overlaid by the
# bind-mount from docker-compose, but copying here makes the image
# self-contained for `docker run` without compose.
COPY . .

EXPOSE 3000

# --host 0.0.0.0 is required so the dev server binds to all interfaces
# inside the container; otherwise the host port-forward cannot reach it.
# Port 3000 follows FR-015 (local dev convention).
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
