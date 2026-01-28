# Docker Deployment Guide

This guide details how to build your application, push the Docker image to Docker Hub, and deploy it using the production Docker Compose file.

## Prerequisites

1.  **Docker Desktop** installed and running.
2.  A **Docker Hub** account (create one at [hub.docker.com](https://hub.docker.com/)).
3.  Command line (Terminal/PowerShell).

---

## 1. Build and Push the Image

You need to build a single Docker image that will be used by all three services (App, API, Scheduler).

### Step 1.1: Login to Docker Hub
Run this command and enter your credentials:
```bash
docker login
```

### Step 2: Build the Image
Replace `yourusername` with your actual Docker Hub username.
```bash
# Build the image with a tag (e.g., 'latest')
docker build -t yourusername/finance-app:latest .
```

### Step 3: Push the Image
Upload the built image to Docker Hub so your production server can access it.
```bash
docker push yourusername/finance-app:latest
```

---

## 2. Deploying on Production (e.g., Proxmox / VPS)

### Step 1: Prepare the Server
1.  Ensure Docker and Docker Compose are installed on the server.
2.  Create a project directory (e.g., `~/finance-app`).
3.  Copy the `docker-compose.prod.yml` file to this directory.

### Step 2: Prepare the Data Directory
To ensure your database persists even if containers are recreated, create a local folder for data:
```bash
mkdir finance_data
```
*(Optional)* If you have an existing `finance.db` you want to use, upload it into this `finance_data` folder.

### Step 3: Run the Application
You need to tell Docker Compose which image to use. You can do this by setting an environment variable or editing the file directly.

**Option A: Run inline (Recommended)**
```bash
# Replace 'yourusername' with your actual username
DOCKER_IMAGE_NAME=yourusername/finance-app:latest docker-compose -f docker-compose.prod.yml up -d
```

**Option B: Edit the file**
1.  Open `docker-compose.prod.yml`.
2.  Find lines like `image: ${DOCKER_IMAGE_NAME:-finance-app:latest}`.
3.  Change them to `image: yourusername/finance-app:latest`.
4.  Run: `docker-compose -f docker-compose.prod.yml up -d`

---

## 3. Useful Commands

| Action | Command |
| :--- | :--- |
| **Check Logs** | `docker-compose -f docker-compose.prod.yml logs -f` |
| **Stop App** | `docker-compose -f docker-compose.prod.yml down` |
| **Update App** | 1. `docker pull yourusername/finance-app:latest`<br>2. `docker-compose -f docker-compose.prod.yml up -d` |

---

## 4. Application Details

### 4.1 FastAPI Backend API

The application includes a FastAPI backend that provides programmatic access to your portfolio data.

**Base URL (Development):** `http://localhost:8000` (or the exposed port on your server)
**Interactive Docs:** `http://localhost:8000/docs`

**Endpoints:**

*   **`GET /api/v1/assets`**: Retrieve a list of all assets in the portfolio. Includes calculated daily price and percentage changes.
*   **`GET /api/v1/assets/{owner}`**: Retrieve assets for a specific owner (e.g., 'Vivek'). Includes calculated daily changes.
*   **`GET /api/v1/history`**: Retrieve the historical total net worth data.
*   **`GET /api/v1/transactions`**: Retrieve all investment transaction records, ordered by date.
*   **`GET /api/v1/changes`**: Retrieve the latest daily and monthly portfolio change summary. This data is persisted in the `portfolio_change_history` table by the background updater.
*   **`GET /api/v1/changes/history`**: Retrieve the full historical log of daily and monthly portfolio changes, ordered by date. This endpoint is ideal for external dashboards.
*   **`POST /api/v1/trigger-background-job`**: Trigger the background updater script (`background_updater.py`) to run a one-time update of prices and calculations. Returns a `202 Accepted` status.

### 4.2 Background Updater (`background_updater.py`)

This script is crucial for keeping your portfolio data up-to-date and providing intelligent insights. It runs automatically on a schedule (e.g., hourly in production via Docker Compose) or can be manually triggered via the API or Streamlit UI.

**Key Functions:**

*   **Price Fetching**: Connects to Yahoo Finance to fetch the latest prices for all tracked assets, as well as historical prices (e.g., 30 days ago for monthly change calculations).
*   **Daily/Monthly Change Calculation**: Calculates the daily percentage change for individual assets and aggregates these into overall daily and monthly changes for the entire portfolio.
*   **AI-Powered Ticker Correction**: Utilizes a Groq AI model (Llama 3) to suggest corrections for invalid or unresolvable asset ticker symbols.
*   **AI Market Summary & Notifications**: Employs the Groq AI model to analyze significant market movements within your portfolio, generating a concise summary. This summary is sent as a push notification via Gotify if predefined thresholds are met (e.g., significant portfolio value change).
*   **History Persistence**: Crucially, after updating all prices and calculating portfolio-wide changes, it **persists these aggregated daily and monthly change metrics into the `portfolio_change_history` database table**. This table (`date`, `daily_change_value`, `daily_change_percent`, `monthly_change_value`, `monthly_change_percent`) serves as a historical record for easy querying by APIs and external dashboards.

### Notes on `DB_FILE_PATH`
The application is configured to look for the environment variable `DB_FILE_PATH`.
- In **Development** (`docker-compose.yml`), it defaults to the local `finance.db`.
- In **Production** (`docker-compose.prod.yml`), it is set to `/data/finance.db`, which maps to your host's `./finance_data` folder. This ensures your data is safe and backed up on the host.

### Database Schema Updates
The application now includes a new table:
*   **`portfolio_change_history`**: Stores a daily record of the total portfolio's daily and monthly value and percentage changes. This enables historical tracking of performance metrics separate from just total net worth.
