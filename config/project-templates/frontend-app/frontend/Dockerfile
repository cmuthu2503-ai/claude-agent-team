# Frontend for {{PROJECT_NAME}} — Vite + React on Node 20.
FROM node:20-alpine

WORKDIR /app

RUN apk add --no-cache wget

COPY package.json ./
RUN npm install --no-audit --no-fund

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]
