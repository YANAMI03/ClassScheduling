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
   - Set `SITE_URL` to the base address you access the app from (e.g., `http://127.0.0.1:5000` or your LAN address `http://192.168.254.108:5000`). This ensures verification email confirmation links route back to your exact address.
   - In Supabase Dashboard > **Authentication** > **URL Configuration**, add your base URLs and wildcard redirect patterns to the Redirect URLs allow-list (e.g., `http://127.0.0.1:5000/**`, `http://localhost:5000/**`, `http://192.168.254.108:5000/**`).
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
SITE_URL=http://127.0.0.1:5000
FLASK_DEBUG=1
PORT=5000
# Optional email domain restriction (e.g. ALLOWED_SIGNUP_EMAIL_DOMAINS=neust.edu.ph)
ALLOWED_SIGNUP_EMAIL_DOMAINS=
```

> **Note on `SITE_URL`:** Set this to the exact base URL you use to open the app (for example `http://127.0.0.1:5000` for local access or `http://192.168.254.108:5000` for LAN access). The app uses this to dynamically generate email confirmation redirect URLs (`/auth/callback`).

> **Note on `ALLOWED_SIGNUP_EMAIL_DOMAINS`:** Optional comma-separated list of approved email domains for open sign-up (e.g., `neust.edu.ph,cict.neust.edu.ph`). When set, only users with matching email domains can register. If left blank or omitted, registration is open to all valid email addresses.

### Supabase Auth & Email Verification Setup
To ensure signup verification emails and links function correctly:
1. **Authentication > URL Configuration**:
   - **Site URL**: Set to your primary address (e.g. `http://127.0.0.1:5000` or `http://192.168.254.108:5000`).
   - **Redirect URLs (Allow-list)**: Add all addresses you access the app from:
     - `http://127.0.0.1:5000/**`
     - `http://localhost:5000/**`
     - `http://192.168.254.108:5000/**`
     - `http://127.0.0.1:5000/auth/callback`
     - `http://localhost:5000/auth/callback`
     - `http://192.168.254.108:5000/auth/callback`
2. **Authentication > Providers > Email**:
   - Ensure **Confirm email** is enabled.
   - Email template must contain `{{ .ConfirmationURL }}`.
3. **Authentication > SMTP Settings (Custom Provider)**:
   - Supabase's built-in default email service has a strict rate limit (~3-4 emails/hour) intended only for testing. For production or repeated testing, configure a custom SMTP provider (such as Resend, Brevo, SendGrid, or Gmail SMTP with an App Password).

### 5. Start the Application
```bash
python app.py
```
Open your browser and navigate to [http://localhost:5000](http://localhost:5000) or [http://192.168.254.108:5000](http://192.168.254.108:5000).

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

---

## Role Permissions Matrix (Schedule Archive)

| Action | Admin | Chair (formerly Scheduler) | Description & Scoping |
| :--- | :---: | :---: | :--- |
| **Archive Active Schedule** | Allowed (All programs) | **Allowed (Own program only)** | Direct archive without deletion request or admin approval. Sets `archive = TRUE`, `archived_at = now()`, and fresh `archive_batch_id`. A Chair attempting another program receives HTTP 403. |
| **Restore Archived Schedule** | Allowed (All programs) | **Allowed (Own program only)** | Conflict-guarded against other programs' room and professor schedules. Blocked if an active schedule already exists for the program & semester. Reactivates batch atomically (`archive = FALSE`, clears timestamp and batch ID). |
| **Permanent Delete Archive** | **Blocked (HTTP 403 / No UI)** | **Blocked (HTTP 403 / No UI)** | Permanent deletion of archived batches is disabled. Neither Admin nor Chair can view or call deletion. |

