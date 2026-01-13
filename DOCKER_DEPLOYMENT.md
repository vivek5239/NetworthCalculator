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

## Notes on `DB_FILE_PATH`
The application is configured to look for the environment variable `DB_FILE_PATH`.
- In **Development** (`docker-compose.yml`), it defaults to the local `finance.db`.
- In **Production** (`docker-compose.prod.yml`), it is set to `/data/finance.db`, which maps to your host's `./finance_data` folder. This ensures your data is safe and backed up on the host.
