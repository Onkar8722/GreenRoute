# 📁 Project Structure Guide

## Overview

This document explains the organized directory structure of the GreenRoute project.

---

## Directory Breakdown

### 🗂️ `/`  - Root Directory
**Purpose**: Project root with main configuration files

| File | Purpose |
|------|---------|
| `README.md` | Main project documentation (start here!) |
| `Dockerfile` | Container image definition |
| `requirements.txt` | Python package dependencies |
| `.gitignore` | Git ignore rules |

**How to use**: 
- Read [`README.md`](README.md) for overview and quick start
- Use [`Dockerfile`](Dockerfile) for containerization
- Install dependencies: `pip install -r requirements.txt`

---

### 🔧 `/src` - Source Code (Application Core)
**Purpose**: All Python application code for the GreenRoute backend

#### Main Application
```
src/
├── app.py                              ⭐ MAIN ENTRY POINT
├── __init__.py                         # Package initialization
└── cache.py, db.py, models.py         # Core utilities
```

**Usage**: `python src/app.py`

#### Machine Learning Modules
```
src/
├── forecasting.py                     # 📊 Bin fill forecasting model
├── genetic_algorithm.py               # 🧬 Route optimization
└── collection_window_predictor.py    # 🔮 ML predictions
```

**Features**:
- `forecasting.py`: Predicts waste levels 24h ahead
- `genetic_algorithm.py`: Optimizes routes (up to 30% better)
- `collection_window_predictor.py`: Predicts best collection times

#### Fleet Management
```
src/
├── vehicle_management.py              # 🚗 Vehicle & driver schemas
└── fleet_management_api.py            # 🚚 Fleet operations logic
```

**Features**:
- Vehicle tracking and status
- Driver assignment and performance
- Route scheduling
- Real-time progress monitoring

#### IoT/External Integration
```
src/
└── mqtt_listener.py                   # 🔌 MQTT broker connection
```

**Features**: Connects to IoT devices for real-time bin data

#### Configuration Reference
```
src/
├── cache.py                           # Caching layer (Redis wrapper)
├── db.py                              # PostgreSQL connection manager
└── models.py                          # Pydantic data models
```

---

### ⚙️ `/config` - Configuration & Environment
**Purpose**: Application configuration and environment settings

```
config/
├── config.py                          # Main configuration class
├── .env.example                       # Environment template (COPY THIS!)
└── .dockerignore                      # Docker ignore rules
```

**How to use**:
```bash
# 1. Copy example to .env
copy config\.env.example config\.env

# 2. Edit config/.env with your settings
DATABASE_URL=postgresql://user:pass@db:5432/greenroute
FLASK_ENV=production
CACHE_ENABLED=true

# 3. App auto-loads from config/.env
```

**Key Settings**:
- `DATABASE_URL`: PostgreSQL connection string
- `FLASK_ENV`: development | production
- `CACHE_ENABLED`: Enable Redis caching
- `MQTT_BROKER`: IoT device connection

---

### 🧪 `/tests` - Test Suite
**Purpose**: Comprehensive test coverage for all features

```
tests/
├── test_all_features.py               # 🎯 Run ALL tests (comprehensive)
├── test_api.py                        # 🔗 REST API endpoint tests
├── test_routes.py                     # 🛣️ Route optimization tests
├── test_routing.py                    # 🗺️ Routing algorithm tests
└── test_dashboard.py                  # 📊 Dashboard data tests
```

**How to run**:
```bash
# Run all tests
python -m pytest tests/

# Or run individual test suite
python tests/test_all_features.py
python tests/test_api.py
python tests/test_routes.py

# Expected: ✅ 100% pass rate
```

**What's tested**:
- ✅ Route optimization (GA algorithm)
- ✅ Real-time vehicle tracking
- ✅ Fleet scheduling
- ✅ ML predictions
- ✅ Driver mobile app
- ✅ What-if scenarios
- ✅ System health monitoring

---

### 🚀 `/scripts` - Utility Scripts
**Purpose**: Helper scripts for setup, training, simulation, and monitoring

#### Setup & Initialization
```
scripts/
├── init_db.py                         # 🗄️ Initialize PostgreSQL database
└── setup_simple.py                    # ⚡ Quick setup alternative
```

**Usage**:
```bash
# Full database setup
python scripts/init_db.py

# Or simple setup
python scripts/setup_simple.py
```

#### Data & Training
```
scripts/
├── generate_training_data.py          # 📈 Create sample data
└── train_forecaster.py                # 🤖 Train ML models
```

**Usage**:
```bash
# Generate training data (Creates sample bin data)
python scripts/generate_training_data.py

# Train forecasting model
python scripts/train_forecaster.py
```

#### Monitoring & Simulation
```
scripts/
├── simulate_iot.py                    # 🔌 Simulate IoT devices
├── monitor_system.py                  # 📡 Monitor system health
└── verify_setup.py                    # ✓ Verify installation
```

**Usage**:
```bash
# Start IoT simulation (in separate terminal)
python scripts/simulate_iot.py

# Monitor system performance
python scripts/monitor_system.py

# Verify all components work
python scripts/verify_setup.py
```

#### Migration & Utilities
```
scripts/
├── migrate_data.py                    # 🔄 Data migration utility
├── start_services.ps1                 # 🪟 Start services (Windows)
└── start_services.sh                  # 🐧 Start services (Unix)
```

---

### 🖥️ `/frontend` - Web UI & Assets
**Purpose**: All user interface and visual assets

#### Web Templates (HTML)
```
frontend/templates/
├── index.html                         # 📊 Main dashboard
├── fleet.html                         # 🚚 Fleet management page
├── tracking.html                      # 📍 Real-time tracking map
├── driver.html                        # 👨‍💼 Driver mobile app
├── scenarios.html                     # 🔮 What-if analysis
└── routes.html                        # 🛣️ Route optimizer
```

**Responsive Design**: All UIs work on mobile (400px) to desktop (2000px)

#### Static Assets (CSS, JS, Images)
```
frontend/static/
├── css/                               # Stylesheets
├── js/                                # JavaScript
└── images/                            # Image assets
```

#### Map Data & Resources
```
frontend/
├── maps/                              # Map tiles and GeoJSON
└── data/                              # Sample datasets
    └── pune_smart_bin_dataset.csv    # Real waste data
```

#### Machine Learning Models (Saved)
```
frontend/models/
├── forecast_model.pkl                 # Forecasting model (300MB)
└── collection_window_model.pkl        # Prediction model (100MB)
```

**Note**: Models are trained and cached here for fast loading

---

### 📚 `/docs` - Documentation
**Purpose**: Complete project documentation and database schemas

```
docs/
├── PROJECT_README.md                  # 📖 COMPLETE DOCUMENTATION
│   - System architecture
│   - Feature descriptions
│   - API reference
│   - Database schema
│   - ML model details
│   - Deployment guide
│   - Development guide
│
├── setup_db.sql                       # 🗄️ Database initialization SQL
└── database_schema.sql                # 🔗 Full schema definitions
```

**How to use**:
- Read [`PROJECT_README.md`](docs/PROJECT_README.md) for comprehensive documentation
- Use SQL files for database setup: `psql -f docs/setup_db.sql`

---

### 📦 `/assets` - Assets & Resources
**Purpose**: Project assets not used in runtime

```
assets/
```

Currently empty - use for:
- Project logos
- Marketing materials
- Presentations
- Non-code resources

---

## File Navigation Map

### 🎯 Quick Access by Task

#### "I want to start the application"
```
1. Read: README.md
2. Edit: config/.env
3. Run: python src/app.py
4. Visit: http://localhost:5000
```

#### "I want to set up the database"
```
1. Review: docs/setup_db.sql
2. Run: python scripts/init_db.py
3. Verify: python scripts/verify_setup.py
```

#### "I want to train ML models"
```
1. Generate data: python scripts/generate_training_data.py
2. Train: python scripts/train_forecaster.py
3. Models saved in: frontend/models/
```

#### "I want to run tests"
```
1. Run all: python tests/test_all_features.py
2. Or specific: python tests/test_api.py
3. Check: 100% pass rate expected
```

#### "I want to understand the code"
```
1. Overview: README.md
2. Components: docs/PROJECT_README.md
3. Code: src/*.py (well-documented)
```

#### "I want to deploy"
```
1. Read: docs/PROJECT_README.md#deployment
2. Use: Dockerfile
3. Or: docker-compose.yml
```

---

## 📊 File Organization Statistics

| Category | Count | Lines |
|----------|-------|-------|
| Python Modules | 10 | 2,500+ |
| Test Suites | 5 | 800+ |
| HTML Templates | 6 | 1,500+ |
| Configuration | 3 | 200+ |
| Scripts | 10 | 600+ |
| Documentation | 3 | 3,000+ |
| **Total** | **37** | **8,600+** |

---

## 🔄 Development Workflow

### Typical Development Session

```
1. Start application
   └─ python src/app.py

2. Open browser
   └─ http://localhost:5000

3. Make changes
   ├─ Edit src/*.py
   ├─ Edit frontend/templates/*.html
   └─ or Run scripts/simulate_iot.py

4. Test changes
   └─ python tests/test_all_features.py

5. Deploy
   ├─ docker build -t greenroute .
   └─ docker run -p 5000:5000 greenroute
```

---

## 🎓 Learning Path

### For New Developers

**Week 1**:
- [ ] Read [`README.md`](README.md)
- [ ] Review [`docs/PROJECT_README.md`](docs/PROJECT_README.md)
- [ ] Run the application locally
- [ ] Explore web interfaces

**Week 2**:
- [ ] Study source code in `/src`
- [ ] Review test cases in `/tests`
- [ ] Run and understand tests

**Week 3**:
- [ ] Modify features in `/src`
- [ ] Write new tests
- [ ] Deploy using Docker

**Ongoing**:
- [ ] Contribute to codebase
- [ ] Improve documentation
- [ ] Add new features

---

## 🆘 Need Help?

- **Quick Start**: See [`README.md`](README.md)
- **Full Docs**: See [`docs/PROJECT_README.md`](docs/PROJECT_README.md)
- **Issues**: Check [`tests/`](tests/)
- **Configuration**: Check [`config/`](config/)

---

**Last Updated**: April 14, 2026
**Structure Version**: 1.0
**Status**: ✅ Production-Ready
