# TaskUp Project Context

## What This Project Does
TaskUp is a mobile-focused assignment manager that connects to Moodle (Ruppin) and helps students track tasks in a cleaner workflow.

It provides:
- Moodle login through a custom backend
- Assignment synchronization from Moodle courses
- Task status management (pending, submitted, archived)
- Basic user session management with access and refresh tokens
- UI settings for language, theme, and notification preferences

## High-Level Architecture
This workspace contains two main parts:

1. Backend API (FastAPI, Python)
- Folder: TaskUp Server
- Responsibilities:
  - Authenticate users against Moodle
  - Pull courses and assignments from Moodle Web Services
  - Save and query assignments in PostgreSQL
  - Expose REST endpoints for the mobile app
  - Manage access/refresh token lifecycle

2. Frontend Mobile App (Expo React Native)
- Folder: TaskUp_UI/TaskUp_UI
- Responsibilities:
  - User login screen
  - Dashboard with tabs for pending, submitted, and archived tasks
  - Actions to mark tasks submitted and archive tasks
  - Settings screen (dark mode, language, notification preferences)

## Main User Flow
1. User enters Moodle credentials in the mobile app.
2. Backend calls Moodle token endpoint and validates login.
3. Backend fetches user profile, courses, and assignments.
4. Backend stores/updates data in PostgreSQL.
5. Backend returns app tokens (access + refresh).
6. Mobile app loads assignment lists using Bearer auth.
7. User can mark assignments submitted, move to archive, and resync data.

## Core Features Implemented
- Moodle integration:
  - core_webservice_get_site_info
  - core_enrol_get_users_courses
  - mod_assign_get_assignments
  - mod_assign_get_submission_status
- Assignment lifecycle views:
  - Pending
  - Submitted
  - Archived
- Sync endpoint to refresh assignments from Moodle
- Auto-archiving logic for old courses in backend query path
- JWT access tokens and database-backed refresh tokens
- Encrypted storage of Moodle token using Fernet
- Hebrew/English translations and RTL-aware UI behavior
- Animated and themed mobile UI (light/dark)

## Backend Key Files
- main.py: FastAPI app, route definitions, Moodle request logic
- database.py: PostgreSQL access layer and business data operations
- security.py: JWT and encryption helpers
- models.py: Pydantic request models
- schema.sql: database schema
- docker-compose.yml: local Postgres and PgAdmin services
- requirements.txt: backend dependencies

## Frontend Key Files
- src/App.jsx: app shell and navigation
- src/api.js: API client functions
- src/pages/LoginPage.jsx: login flow
- src/pages/DashBoard.jsx: tabs, task lists, actions, sync usage
- src/pages/SettingsPage.jsx: UI settings
- src/translations.js: i18n strings
- src/config.js: backend BASE_URL selection

## API Surface (Current)
Auth:
- POST /api/login
- POST /api/refresh
- POST /api/logout

Assignments:
- GET /api/assignments/pending
- GET /api/assignments/submitted
- GET /api/assignments/archived
- PATCH /api/assignments/{assignment_id}/submit
- PATCH /api/assignments/{assignment_id}/archive
- POST /api/assignments/sync

Notifications:
- GET /api/notifications/settings
- PATCH /api/notifications/settings

## Data Model Summary
Main tables:
- users
- assignments
- user_assignments
- refresh_tokens
- notification_settings

Design intent:
- Keep assignment metadata normalized in assignments
- Keep user-specific state in user_assignments
- Separate session security into refresh_tokens
- Store notification preferences per user

## Environment and Runtime Notes
Backend:
- Uses .env for DB config and cryptographic secrets
- Uses FastAPI + Uvicorn
- Uses PostgreSQL (local via docker-compose or remote managed DB)

Frontend:
- Uses Expo scripts from package.json
- Chooses BASE_URL by dev/prod mode in src/config.js

## Current Gaps and Scaling Notes
The app works as an MVP with a strong base, but these areas are still needed for full production scale:
- Add resilient request handling to Moodle calls (timeouts, retries, circuit-break behavior)
- Add login/refresh rate limiting and abuse protection
- Implement refresh-token rotation and replay detection
- Restrict CORS in production
- Add background workers/queues for heavy sync operations
- Add structured logging, metrics, and alerting
- Persist task notes server-side (currently local behavior)
- Wire settings screen fully to backend notification preferences
- Add automated tests (unit, integration, end-to-end)
- Add migration tooling/versioned schema management

## Quick Start (Developer)
Backend:
1. Install Python dependencies from requirements.txt
2. Start Postgres with docker-compose (optional local)
3. Run FastAPI app with Uvicorn

Frontend:
1. Install npm dependencies in TaskUp_UI/TaskUp_UI
2. Start Expo with npm start
3. Run on emulator/device

## Audience Notes for LLMs
When modifying this project:
- Treat main.py, database.py, and security.py as backend source of truth
- Treat src/api.js and Dashboard/Login pages as frontend behavior source of truth
- Keep API route contracts backward-compatible unless explicitly changing both client and server
- Avoid exposing secrets from environment files in generated output
- Validate cross-impact between backend endpoints and frontend API calls before edits
