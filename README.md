# UniTask — Backend 

> Never miss a Moodle deadline again.

UniTask(Formely TaskUp) is a mobile assignment manager for university students that pulls your Moodle courses into a clean, actionable interface — with smart reminders, submission tracking, and real-time notifications when assignments are posted or deadlines change.

This repository contains the backend API powering the TaskUp mobile app.

---

## Features

- **Moodle Single Sign-On** — log in with your university credentials, no new account needed
- **Multi-institution support** — Ruppin Academic Center and Ben-Gurion University
- **Assignment lifecycle** — track every assignment through Pending → Submitted → Archived
- **Auto-discovery engine** — detects new assignments and due-date changes across courses automatically
- **Smart push notifications** — customizable reminder thresholds (e.g. 24h, 2h before deadline)
- **Late submission tracking** — flags assignments submitted after their due date
- **Per-assignment notes** — quick reminders attached to individual assignments
- **Hebrew & English support** — localized notification messages

---

## Architecture

The API is a FastAPI application deployed on Azure App Service, backed by a PostgreSQL database on Azure.

**Background jobs** run continuously inside the process via APScheduler:

- **Discovery Engine (External Scheduler)** — A serverless Azure Function triggers this engine every 30 minutes. It employs a crowdsourced approach: the system selects one representative user per course to fetch assignments from Moodle. This data is then synchronized across all enrolled students' accounts. Any detected new assignments or due-date changes trigger broadcast push notifications to the entire course community via the Expo Push Service.

- **Reminder engine** — runs every 10 minutes around the clock. For each user, checks upcoming deadlines against their notification preferences and sends a push notification at each configured threshold (e.g. 24h out, 2h out). Verifies submission status against Moodle in real-time before sending to avoid false alerts.

**Authentication** uses short-lived JWT access tokens (60 min) combined with long-lived refresh tokens (4 years) stored in the database. Moodle tokens are encrypted at rest using Fernet symmetric encryption.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI |
| Server | Uvicorn (ASGI) |
| Database | PostgreSQL (Azure Database) |
| Auth | JWT (python-jose) + Fernet encryption |
| Background Jobs | APScheduler |
| Push Notifications | Expo Push API |
| Deployment | Azure App Service |
| CI/CD | GitHub Actions |
| Moodle Integration | Moodle Web Services REST API |


---

## Visit out website

- **Mobile App** — [UniTask)](https://unitask.net/)
