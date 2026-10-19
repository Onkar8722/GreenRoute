-- GreenRoute Database Setup Script
-- Creates database, user, and permissions

-- Create database
CREATE DATABASE greenroute_db;

-- Create user
CREATE USER greenroute_user WITH PASSWORD 'greenroute_pass';

-- Grant all privileges on database
GRANT ALL PRIVILEGES ON DATABASE greenroute_db TO greenroute_user;

-- Connect to the database and grant schema privileges
\c greenroute_db

-- Grant schema privileges
GRANT ALL PRIVILEGES ON SCHEMA public TO greenroute_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO greenroute_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO greenroute_user;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO greenroute_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO greenroute_user;

-- Display confirmation
\echo '=================================================='
\echo '✅ GreenRoute Database Setup Complete!'
\echo '=================================================='
\echo 'Database: greenroute_db'
\echo 'User: greenroute_user'
\echo 'Password: greenroute_pass'
\echo '=================================================='
