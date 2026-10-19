# GreenRoute Service Startup Script (PowerShell)
# Starts PostgreSQL, Redis, and MQTT containers for local development
# Run: .\start_services.ps1

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Starting GreenRoute Services" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check Docker
Write-Host "Checking Docker installation..." -ForegroundColor Yellow
try {
    $dockerVersion = docker --version 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker not found"
    }
    Write-Host "✅ Docker: $dockerVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ ERROR: Docker is not installed" -ForegroundColor Red
    Write-Host "Install Docker Desktop from: https://www.docker.com/products/docker-desktop" -ForegroundColor Yellow
    exit 1
}

# Check Docker daemon
Write-Host "Checking Docker daemon..." -ForegroundColor Yellow
try {
    docker ps > $null 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon not running"
    }
    Write-Host "✅ Docker daemon is running" -ForegroundColor Green
} catch {
    Write-Host "❌ ERROR: Docker daemon is not running" -ForegroundColor Red
    Write-Host "Please start Docker Desktop and try again" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Cleaning up existing containers..." -ForegroundColor Yellow

# Remove existing containers
@("greenroute-postgres", "greenroute-redis", "greenroute-mqtt") | ForEach-Object {
    docker rm -f $_ 2>$null | Out-Null
}
Start-Sleep -Seconds 1

Write-Host ""
Write-Host "Starting PostgreSQL 15..." -ForegroundColor Yellow
docker run -d `
  --name greenroute-postgres `
  -e POSTGRES_PASSWORD=greenroute_pass `
  -e POSTGRES_USER=greenroute_user `
  -e POSTGRES_DB=greenroute_db `
  -p 5432:5432 `
  postgres:15 | Out-Null

Write-Host "✅ PostgreSQL container started" -ForegroundColor Green
Write-Host "   Waiting for database to be ready (10 seconds)..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

Write-Host ""
Write-Host "Starting Redis 7..." -ForegroundColor Yellow
docker run -d `
  --name greenroute-redis `
  -p 6379:6379 `
  redis:7 | Out-Null

Write-Host "✅ Redis container started" -ForegroundColor Green

Write-Host ""
Write-Host "Starting MQTT Broker (Eclipse Mosquitto)..." -ForegroundColor Yellow
docker run -d `
  --name greenroute-mqtt `
  -p 1883:1883 `
  eclipse-mosquitto:latest | Out-Null

Write-Host "✅ MQTT container started" -ForegroundColor Green

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "All services are running!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host ""

Write-Host "Service Connection Details:" -ForegroundColor Cyan
Write-Host "  PostgreSQL:  localhost:5432"
Write-Host "  Redis:       localhost:6379"
Write-Host "  MQTT:        localhost:1883"
Write-Host ""

Write-Host "Database Credentials:" -ForegroundColor Cyan
Write-Host "  User:     greenroute_user"
Write-Host "  Password: greenroute_pass"
Write-Host "  Database: greenroute_db"
Write-Host ""

Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Open a new terminal"
Write-Host "  2. Run: python app.py"
Write-Host "  3. Visit: http://127.0.0.1:5000"
Write-Host ""

Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  View containers:    docker ps"
Write-Host "  View logs:          docker logs greenroute-postgres"
Write-Host "  Stop services:      docker stop greenroute-postgres greenroute-redis greenroute-mqtt"
Write-Host "  Remove containers:  docker rm greenroute-postgres greenroute-redis greenroute-mqtt"
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
