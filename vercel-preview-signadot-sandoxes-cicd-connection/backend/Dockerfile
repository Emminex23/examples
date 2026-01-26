# Multi-stage Dockerfile for production optimization
FROM node:20-alpine AS deps
WORKDIR /app

COPY package.json bun.lock* ./

# Using npm for Docker compatibility (project has bun.lock but no package-lock.json)
RUN npm install --omit=dev --ignore-scripts && \
    npm cache clean --force

FROM node:20-alpine AS builder
WORKDIR /app

COPY package.json bun.lock* ./
RUN npm install --ignore-scripts && \
    npm cache clean --force

COPY server.js ./
COPY config.js ./

FROM node:20-alpine AS runner
WORKDIR /app

# Create non-root user for security
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nodejs

COPY --from=deps --chown=nodejs:nodejs /app/node_modules ./node_modules
COPY --from=builder --chown=nodejs:nodejs /app/server.js ./
COPY --from=builder --chown=nodejs:nodejs /app/config.js ./
COPY --from=builder --chown=nodejs:nodejs /app/package.json ./

USER nodejs

EXPOSE 3000

ENV NODE_ENV=production
ENV PORT=3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000/health', (r) => {process.exit(r.statusCode === 200 ? 0 : 1)})"

CMD ["node", "server.js"]
