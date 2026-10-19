#!/bin/bash
# GreenRoute Service Startup Script
# Starts PostgreSQL, Redis, and MQTT containers for local development

echo "============================================================"
echo "Starting GreenRoute Services"
echo "============================================================"
echo ""

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed"
    echo "Install Docker Desktop from: https://www.docker.com/products/docker-desktop"
    exit 1
fi

# Check Docker daemon
if ! docker ps > /dev/null 2>&1; then
    echo "ERROR: Docker daemon is not running"
    echo "Please start Docker Desktop and try again"
    exit 1
fi

echo "✅ Docker is running"
echo ""

# Stop and remove existing containers (if any)
echo "Cleaning up existing containers..."
docker rm -f greenroute-postgres 2>/dev/null || true
docker rm -f greenroute-redis 2>/dev/null || true
docker rm -f greenroute-mqtt 2>/dev/null || true
sleep 1

echo ""
echo "Starting PostgreSQL 15..."
docker run -d \
  --name greenroute-postgres \
  -e POSTGRES_PASSWORD=greenroute_pass \
  -e POSTGRES_USER=greenroute_user \
  -e POSTGRES_DB=greenroute_db \
  -p 5432:5432 \
  postgres:15

echo "✅ PostgreSQL container started"
echo "   Waiting for database to be ready..."
sleep 10

echo ""
echo "Starting Redis 7..."
docker run -d \
  --name greenroute-redis \
  -p 6379:6379 \
  redis:7

echo "✅ Redis container started"

echo ""
echo "Starting MQTT Broker (Eclipse Mosquitto)..."
docker run -d \
  --name greenroute-mqtt \
  -p 1883:1883 \
  eclipse-mosquitto:latest

echo "✅ MQTT container started"

echo ""
echo "============================================================"
echo "All services are running!"
echo "============================================================"
echo ""
echo "Service Connection Details:"
echo "  PostgreSQL:  localhost:5432"
echo "  Redis:       localhost:6379"
echo "  MQTT:        localhost:1883"
echo ""
echo "Database Credentials:"
echo "  User:     greenroute_user"
echo "  Password: greenroute_pass"
echo "  Database: greenroute_db"
echo ""
echo "Next: Run the Flask app"
echo "  python app.py"
echo ""
echo "View running containers:"
echo "  docker ps"
echo ""
echo "Stop all containers:"
echo "  docker stop greenroute-postgres greenroute-redis greenroute-mqtt"
echo ""
echo "============================================================"
