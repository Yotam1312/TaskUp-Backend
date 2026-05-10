<div align="center">

# UniTask — Backend

**Never miss a Moodle deadline again.**

UniTask(Formely TaskUp) is a mobile assignment manager for university students that pulls Moodle courses into a clean, actionable interface — with smart reminders, submission tracking, and real-time notifications when assignments are posted or deadlines change.

[![Website](https://img.shields.io/badge/website-unitask.net-blue)](https://unitask.net/)
[![App Store](https://img.shields.io/badge/iOS-App%20Store-black?logo=apple)](https://unitask.net/)
[![Google Play](https://img.shields.io/badge/Android-Google%20Play-green?logo=google-play)](https://unitask.net/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Azure-336791?logo=postgresql&logoColor=white)](https://azure.microsoft.com)
[![Azure](https://img.shields.io/badge/Deployed-Azure%20App%20Service-0078D4?logo=microsoft-azure&logoColor=white)](https://azure.microsoft.com)

[Website](https://unitask.net/) 
</div>

---

## Overview

UniTask (formerly TaskUp) is a cross-platform mobile application available on **iOS and Android**, built for students at Israeli universities to manage their Moodle assignments effortlessly. Instead of logging into Moodle manually and hunting for upcoming deadlines, UniTask aggregates everything into a single app with smart push notifications and an intuitive assignment lifecycle.

This repository contains the **backend API** that powers the UniTask mobile app.

**Supported institutions:**
- Ben-Gurion University of the Negev (BGU)
- Ruppin Academic Center

---

## Features

| Feature | Description |
|---|---|
| **Moodle Single Sign-On** | Log in with your existing university credentials — no new account needed |
| **Multi-institution support** | Seamlessly supports Ruppin Academic Center and Ben-Gurion University from a single backend |
| **Assignment lifecycle tracking** | Every assignment progresses through `Pending → Submitted → Archived` with full status persistence |
| **Auto-discovery engine** | Crowdsourced engine detects new assignments and due-date changes across all courses in real time |
| **Smart push notifications** | Fully customizable reminder thresholds (e.g. 24h, 2h before deadline) per user |
| **Late submission detection** | Automatically flags assignments submitted after their due date |
| **Per-assignment notes** | Attach quick reminders or notes to individual assignments (up to 50 characters) |
| **Hebrew & English support** | Fully localized notification messages in both Hebrew and English |
| **Quiz support** | Tracks Moodle quizzes alongside regular assignments |
| **Multi-device support** | Push tokens scoped per user — device changes are handled cleanly |

---

## Architecture

The backend is a **FastAPI** application deployed on **Azure App Service**, backed by a managed **PostgreSQL** database on Azure.

```
┌─────────────────────────────────────────────────────────┐
│                    UniTask Mobile App                   │
│              (React Native · iOS · Android)             │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTPS / REST API
                            ▼
┌─────────────────────────────────────────────────────────┐
│              FastAPI Backend (Azure App Service)        │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ Auth Endpoints│  │  Assignment  │  │ Notification  │ │
│  │  /api/login  │  │  Endpoints   │  │   Endpoints   │ │
│  │  /api/refresh│  │ /api/assign- │  │ /api/notif-   │ │
│  │  /api/logout │  │  ments/*     │  │ ications/*    │ │
│  └──────────────┘  └──────────────┘  └───────────────┘ │
│                                                         │
│  ┌──────────────────────┐  ┌──────────────────────────┐ │
│  │   Discovery Engine   │  │     Reminder Engine      │ │
│  │  (every 30 minutes)  │  │   (every 10 minutes)     │ │
│  │  External Azure Func │  │   APScheduler internal   │ │
│  └──────────────────────┘  └──────────────────────────┘ │
└───────────┬─────────────────────────┬────────────────────┘
            │                         │
            ▼                         ▼
┌───────────────────┐     ┌────────────────────────────┐
│  PostgreSQL DB    │     │  Moodle Web Services API   │
│  (Azure Managed)  │     │  (BGU / Ruppin instances)  │
└───────────────────┘     └────────────────────────────┘
                                      │
                                      ▼
                          ┌────────────────────────┐
                          │  Expo Push Service     │
                          │  (iOS & Android push)  │
                          └────────────────────────┘
```

### Background Engines

**Discovery Engine — triggered every 30 minutes via Azure Function Timer:**

Employs a crowdsourced approach to minimize Moodle API load. For each course, one representative user's credentials are used to fetch the latest assignments and quizzes from Moodle. The results are synchronized across all enrolled students. Any newly detected assignments or due-date changes trigger broadcast push notifications to the entire course.

**Reminder Engine — runs every 10 minutes, 24/7:**

Scans all pending assignments against each user's configured notification thresholds. Before sending a reminder, it verifies submission status against Moodle in real time to avoid false alerts. Tracks which thresholds have already been notified to prevent duplicate sends.

### Authentication Flow

```
User → [Moodle credentials] → Backend
         ↓
Backend → [Validate against Moodle API]
         ↓
Backend → [Encrypt Moodle token with Fernet, store in DB]
         ↓
Backend → [Issue JWT access token (60 min) + refresh token (4 years)]
         ↓
App stores tokens → Uses JWT for all API calls → Refresh when expired
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI |
| Server | Uvicorn (ASGI) |
| Database | PostgreSQL (Azure Database for PostgreSQL) |
| Authentication | JWT via `python-jose` + Fernet symmetric encryption |
| Background Jobs | APScheduler (in-process) + Azure Function (external timer) |
| Push Notifications | Expo Push API (`exponent-server-sdk`) |
| Deployment | Azure App Service |
| Moodle Integration | Moodle Web Services REST API |

---

## Project Structure

```
TaskUp-Backend/
├── main.py          # FastAPI app, all route handlers
├── database.py      # PostgreSQL data access layer
├── security.py      # JWT & Fernet encryption helpers
├── models.py        # Pydantic request/response models
├── schema.sql       # Full database schema
├── requirements.txt # Python dependencies
├── docker-compose.yml  # Local development DB setup
└── .env             # Environment variables (not committed)
```

---

## Team

| Name | Role |
|---|---|
| **Harel Cohen** | Fullstack Developer — System Operations & Networking |
| **Yotam Shpilman** | Software & Backend Developer |

---

## Links

- **Website:** [unitask.net](https://unitask.net/)
- **iOS App:** [Download on the App Store](https://unitask.net/)
- **Android App:** [Get it on Google Play](https://unitask.net/)
- **Frontend Repository:** [UniTask-Frontend](https://github.com/Yotam1312/TaskUp-Frontend)