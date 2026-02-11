-- Seed data for testing
INSERT INTO users (name, email) VALUES
    ('Alice Johnson', 'alice@example.com'),
    ('Bob Smith', 'bob@example.com'),
    ('Carol Williams', 'carol@example.com'),
    ('David Brown', 'david@example.com'),
    ('Eve Davis', 'eve@example.com')
ON CONFLICT (email) DO NOTHING;
