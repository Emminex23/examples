/**
 * Application configuration loaded from environment variables
 * Only PORT is configurable via environment variables
 */
const config = {
  port: process.env.PORT || 3000,
};

module.exports = config;
