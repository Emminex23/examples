const express = require('express');
const config = require('./config');

const app = express();

app.get('/health', (req, res) => {
  res.status(200).json({
    status: 'healthy',
    timestamp: new Date().toISOString(),
    port: config.port
  });
});

app.listen(config.port, () => {
  console.log(`Server is running on port ${config.port}`);
});
