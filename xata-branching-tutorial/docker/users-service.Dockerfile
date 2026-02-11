FROM node:18-alpine

WORKDIR /app

COPY pkg/users-service/package*.json ./

RUN npm install --omit=dev

COPY pkg/users-service/app.js ./

EXPOSE 3000

CMD ["node", "app.js"]
