# ClassScheduling

A web-based class scheduling management system built with **Python/Flask**, modern **Vanilla CSS & JavaScript**, and backed by **Supabase** (PostgreSQL with Row Level Security & Auth).

---

## Table of Contents

- [Project Overview](#project-overview)
- [Prerequisites](#prerequisites)
- [Developer Setup Checklist](#developer-setup-checklist)
- [Local Development Setup](#local-development-setup)
- [Docker Development Setup](#docker-development-setup)
- [GitHub Codespaces & Dev Containers](#github-codespaces--dev-containers)
- [Running Tests](#running-tests)
- [Git Workflow & Contribution Guidelines](#git-workflow--contribution-guidelines)
- [Security Guidelines](#security-guidelines)

---

## Project Overview

ClassScheduling streamlines institutional course schedule creation, faculty workload management, section assignments, room utilization, and irregular student curriculum tracking.

- **Backend:** Python / Flask
- **Database & Auth:** Supabase (Remote PostgreSQL with RLS and JWT authentication)
- **Frontend:** Server-rendered Jinja2 templates with responsive Vanilla CSS and Vanilla JavaScript
- **Containerization:** Docker & Docker Compose

---

## Prerequisites

Ensure you have the following installed on your machine:

| Tool | Version Requirement | Purpose |
| :--- | :--- | :--- |
| **Python** | `3.10` - `3.14` (Recommended: `3.11`+) | Backend runtime & dependencies |
| **pip** | `23.0`+ | Python package manager |
| **Docker & Docker Compose** | Docker `20.10`+ / Compose `v2`+ | Containerized local environment |
| **Node.js & npm** *(Optional)* | Node `18`+ / npm `9`+ | Frontend client packages |
| **Supabase Account** | Active Supabase project | Cloud database & authentication |

---

## Developer Setup Checklist

Follow these 7 steps to get up and running:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/ClassScheduling.git
   cd ClassScheduling
   ```
2. **Create your environment configuration:**
   ```bash
   cp .env.example .env
   ```
3. **Add Supabase credentials to `.env`:**
   - Retrieve `SUPABASE_URL` and `SUPABASE_ANON_KEY` from your Supabase Project Settings (`API` tab).
   - *(Optional)* Add `SUPABASE_SECRET_KEY` if testing server-side user administration features.
4. **Choose your development mode:**
   - **Option A:** [Local Python Development](#local-development-setup)
   - **Option B:** [Docker Container Development](#docker-development-setup)
5. **Install dependencies:**
   - Run `pip install -r requirements.txt` (or let Docker handle it).
6. **Start the application:**
   - Local: `python app.py`
   - Docker: `docker compose up --build`
7. **Verify connection:**
   - Open [http://localhost:5000](http://localhost:5000) in your browser and confirm login / schedule dashboards load.

---

## Local Development Setup

### 1. Set Up Python Virtual Environment

#### On Windows (PowerShell / Command Prompt):
```powershell
python -m venv .venv
.venv\Scripts\activate
```

#### On macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Python Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Optional Node Dependencies
If you need to install frontend packages:
```bash
npm install
```
*(Note: The core application runs natively through Flask; Node.js is only used for client-side package management.)*

### 4. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your values:
```bash
# On Windows (cmd):
copy .env.example .env

# On PowerShell / Linux / macOS:
cp .env.example .env
```

Ensure your `.env` contains at minimum:
```env
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_ANON_KEY=your-supabase-anon-key
FLASK_DEBUG=1
PORT=5000
```

### 5. Start the Application
```bash
python app.py
```
Open your browser and navigate to [http://localhost:5000](http://localhost:5000).

---

## Docker Development Setup

Docker allows you to run the application in an isolated, reproducible environment without installing Python packages locally.

> **Note:** The application connects directly to your existing remote Supabase project. No local PostgreSQL container is needed.

### 1. Configure `.env`
Ensure your `.env` file exists and has your Supabase credentials populated.

### 2. Start the Docker Container
```bash
docker compose up --build
```

### 3. Access the Application
Open [http://localhost:5000](http://localhost:5000) in your browser.

- **Live Code Reloading:** Source code directories are volume-mounted into the container. Any edits made to Python files, templates (`.html`), or static assets (`.css`, `.js`) will automatically reload in the running container without requiring a rebuild.
- **View Container Logs:**
  ```bash
  docker compose logs -f web
  ```
- **Stop Containers:**
  ```bash
  docker compose down
  ```

---

## GitHub Codespaces & Dev Containers

This repository includes full **Dev Container** configuration (`.devcontainer/devcontainer.json`).

### Opening in GitHub Codespaces:
1. Navigate to the repository on GitHub.
2. Click the green **Code** button, select the **Codespaces** tab, and click **Create codespace on main**.
3. Once the environment initializes, configure your Supabase secrets in Codespaces Secrets or create a `.env` file from `.env.example`.
4. Start the application:
   ```bash
   python app.py
   ```
5. VS Code will automatically detect port `5000` and prompt you to open the forwarded application in your browser.

### Opening in Local VS Code Dev Containers:
1. Open the project folder in VS Code with the **Dev Containers** extension installed.
2. When prompted, click **Reopen in Container** (or run `Dev Containers: Reopen in Container` from the command palette `Ctrl+Shift+P` / `Cmd+Shift+P`).

---

## Running Tests

Unit tests are written using `pytest`.

To run tests in your local virtual environment:
```bash
# Set PYTHONPATH to project root and execute pytest
pytest tests/
```

To run tests inside the Docker container:
```bash
docker compose exec web pytest tests/
```

---

## Git Workflow & Contribution Guidelines

We follow a structured feature-branch workflow. **Do not commit directly to `main`.**

```text
main
  ↓
create feature branch (feature/your-feature-name)
  ↓
make changes & test locally/Docker
  ↓
commit changes with clear messages
  ↓
push feature branch to remote
  ↓
open Pull Request into main
  ↓
code review & CI checks
  ↓
merge into main
```

### Step-by-Step Commands:

1. **Ensure your `main` branch is up to date:**
   ```bash
   git checkout main
   git pull origin main
   ```
2. **Create a new feature branch:**
   ```bash
   git checkout -b feature/course-tab-filter
   # Or for bug fixes:
   git checkout -b fix/schedule-conflict-check
   ```
3. **Stage and commit your changes:**
   ```bash
   git add .
   git commit -m "feat: enhance course tab count badge styling"
   ```
4. **Push your branch to GitHub:**
   ```bash
   git push -u origin feature/course-tab-filter
   ```
5. **Open a Pull Request (PR):**
   - Go to GitHub and open a PR against `main`.
   - Provide a concise description of what was changed and how to verify.

---

## Security Guidelines

- **Never commit `.env` or any secret keys to version control.** `.gitignore` is configured to block `.env` and credential files.
- **Client Anon Key vs. Secret Key:**
  - `SUPABASE_ANON_KEY` is a public/publishable key. Row Level Security (RLS) policies in Supabase ensure users only access data they are authorized to see.
  - `SUPABASE_SECRET_KEY` (service role) must **never** be exposed in client code, public repos, or Docker images. It is only optionally used server-side for admin user provisioning.
- If a secret is accidentally committed, immediately revoke and rotate it in the Supabase Dashboard.
