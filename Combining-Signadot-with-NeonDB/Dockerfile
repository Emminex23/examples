FROM node:20-slim

WORKDIR /app

COPY package*.json ./
RUN npm ci --only=production

COPY app.js .

EXPOSE 3000

CMD ["npm", "start"]

