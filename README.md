# Job Board MVP

## What was built

A lean full-stack job board built with one FastAPI application serving both server-rendered HTML and JSON API endpoints. It includes a public job browsing and application flow, plus a protected admin area for managing jobs and reviewing applications.

## Features

- Public HTML pages:
  - `GET /`
  - `GET /jobs`
  - `GET /jobs/{job_id}`
  - `POST /jobs/{job_id}/apply`
  - success page after submission
- Admin HTML pages:
  - login/logout
  - dashboard
  - create, edit, open/close, and delete jobs
  - view all applications
  - view applications for a single job
- JSON API:
  - public jobs listing and detail
  - public application submission
  - admin login
  - admin applications listing
  - admin jobs list/create/update/delete
- Automatic sample seed:
  - on startup, if the `jobs` table is empty, one sample open job is created
- Email confirmation with provider fallback:
  - sends via Brevo API if configured
  - falls back to SMTP if Brevo is not configured
  - logs the confirmation instead of crashing when no provider is configured
- Telegram admin bot bonus:
  - webhook-based Telegram bot inside the same FastAPI app
  - secure deep-link connection from the admin dashboard
  - lets connected admins manage jobs and review applications from Telegram

## Tech stack

- Python 3.11+
- FastAPI
- Jinja2 templates
- SQLAlchemy 2.0 async ORM
- PostgreSQL via `DATABASE_URL`
- Redis asyncio client via `REDIS_URL`
- PyJWT for admin authentication
- `pydantic-settings` for configuration
- Telegram Bot API via `httpx`

## PostgreSQL usage

PostgreSQL is the primary application database.

- `jobs` table stores job postings
- `applications` table stores submitted applications
- `applications.job_id` references `jobs.id`
- unique constraint on `(job_id, email)` prevents duplicate applications to the same role from the same email
- emails are normalized to lowercase before insert
- tables are created on FastAPI startup using `Base.metadata.create_all()`

## Redis usage

Redis is used in two meaningful ways.

1. Public jobs cache
- cache key format:
  - `v1:jobs:list:open=true:sort=newest:page={page}:limit={limit}`
- TTL is `60` seconds
- on cache miss, the app queries PostgreSQL, serializes the result, and stores it in Redis
- cache is invalidated after job create, update, toggle, and delete using a scan/delete pattern on:
  - `v1:jobs:list:*`

2. Application anti-spam lock
- lock key format:
  - `v1:apply-lock:job={job_id}:email={normalized_email}`
- implemented with Redis `SET NX EX`
- TTL is `300` seconds
- prevents repeated rapid submissions for the same job/email pair
- PostgreSQL uniqueness is still the final duplicate guard

## Admin auth explanation

- Admin credentials come from environment variables:
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD`
- Login creates a JWT using `JWT_SECRET`
- For browser admin pages, the JWT is stored in an `HttpOnly` cookie
- Protected admin API endpoints also accept `Authorization: Bearer <token>`
- Password comparison uses `hmac.compare_digest`

## Email confirmation explanation

After a successful application submission, the app attempts to send a confirmation email.

- Preferred provider for Railway: Brevo API
  - `BREVO_API_KEY`
  - `BREVO_FROM_EMAIL`
  - `BREVO_FROM_NAME`
- SMTP fallback env vars:
  - `SMTP_HOST`
  - `SMTP_PORT`
  - `SMTP_USERNAME`
  - `SMTP_PASSWORD`
  - `SMTP_FROM_EMAIL`
- If Brevo is configured, the app sends through the Brevo HTTPS API.
- If Brevo is not configured but SMTP values are present, the app falls back to SMTP.
- If neither Brevo nor SMTP is configured, the app logs the confirmation event and continues normally.
- Railway outbound SMTP can fail with network errors, so Brevo API is the preferred provider on Railway.

## Telegram bot bonus

The app includes an optional Telegram admin bot that runs through the same FastAPI app using a webhook endpoint.

How connection works:

1. Log in to the admin dashboard in the browser.
2. Click `Connect Telegram`.
3. The app creates a one-time bind token in Redis and redirects to a Telegram deep link:
   - `https://t.me/{TELEGRAM_BOT_USERNAME}?start=connect_<token>`
4. Press `Start` in Telegram.
5. The bot validates the bind token and stores your Telegram user as an authorized admin in PostgreSQL.

Webhook endpoint:

- `POST /telegram/webhook/{TELEGRAM_WEBHOOK_SECRET}`

Authorization behavior:

- `/whoami` always works and shows:
  - Telegram user id
  - username
  - connected yes/no
- Other admin commands require a connected Telegram account.
- If not connected, the bot replies:
  - `Access denied. Open the admin dashboard and click Connect Telegram, or send /whoami to see your Telegram user ID.`

Supported commands:

- `/start`
- `/whoami`
- `/help`
- `/jobs`
- `/job <id>`
- `/open <id>`
- `/close <id>`
- `/delete <id>`
- `/confirm_delete <id>`
- `/applications`
- `/apps <job_id>`
- `/create`

Interactive job creation:

- `/create` stores temporary per-user state in Redis for 30 minutes
- The bot asks for:
  - title
  - company
  - location
  - employment type
  - salary range
  - description
  - requirements
- Type `cancel` at any step to abort
- Type `yes` at the final confirmation step to create the job

Railway notes:

- Telegram is implemented with HTTPS webhook delivery, not long polling
- No second worker process is required
- On startup, if configured, the app attempts to register the Telegram webhook automatically
- `APP_BASE_URL` must be your live Railway domain

## Environment variables

Copy `.env.example` to `.env` and set the following:

```env
APP_ENV=local
DATABASE_URL=
REDIS_URL=
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
JWT_SECRET=change-me
JWT_EXPIRE_HOURS=12
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=
BREVO_API_KEY=
BREVO_FROM_EMAIL=
BREVO_FROM_NAME=Job Board
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=
TELEGRAM_WEBHOOK_SECRET=
APP_BASE_URL=http://localhost:8000
```

Warning: do not deploy with placeholder `ADMIN_PASSWORD` or `JWT_SECRET` values.

## Local run instructions

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Set up PostgreSQL and Redis locally, then configure `.env`.
4. Start the app:

```bash
uvicorn app.main:app --reload
```

5. Open:
- Public UI: `http://localhost:8000/`
- Admin login: `http://localhost:8000/login`
- API docs: `http://localhost:8000/docs`

## Railway deployment instructions

This project is designed for Railway without Docker.

1. Push the repository to GitHub.
2. In Railway, create a new project from the repo.
3. Add a Railway PostgreSQL service.
4. Add a Railway Redis service.
5. Set environment variables in the web service:
   - `APP_ENV=production`
   - `DATABASE_URL`
   - `REDIS_URL`
   - `ADMIN_USERNAME`
   - `ADMIN_PASSWORD`
   - `JWT_SECRET`
   - `JWT_EXPIRE_HOURS`
   - `BREVO_API_KEY`
   - `BREVO_FROM_EMAIL`
   - `BREVO_FROM_NAME`
   - optional Telegram bot values:
     - `TELEGRAM_BOT_TOKEN`
     - `TELEGRAM_BOT_USERNAME`
     - `TELEGRAM_WEBHOOK_SECRET`
   - optional SMTP values
   - `APP_BASE_URL`
6. Attach the Railway PostgreSQL `DATABASE_URL` and Railway Redis `REDIS_URL` service variables to the FastAPI web service.
7. Railway will install dependencies from `requirements.txt`.
8. `railway.json` config starts the app with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

No `Dockerfile` or `docker-compose.yml` is required.

Telegram setup with BotFather:

1. Create a bot with BotFather and copy the bot token.
2. Note the bot username from BotFather.
3. Set:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_BOT_USERNAME`
   - `TELEGRAM_WEBHOOK_SECRET`
   - `APP_BASE_URL`
4. Deploy or restart the Railway app.
5. Log in to the admin dashboard and click `Connect Telegram`.
6. Press `Start` in the Telegram chat that opens.
7. Send `/help` to confirm the bot is connected.

## Live URLs

- Public app: `https://jobboard-production-e62e.up.railway.app/`
- Admin login: `https://jobboard-production-e62e.up.railway.app/login`
- API docs: `https://jobboard-production-e62e.up.railway.app/docs`

## Submission checklist

- GitHub repo URL
- Live frontend URL
- Backend `/docs` URL
- Railway dashboard screenshot showing the FastAPI, PostgreSQL, and Redis services

## Known limitations

- Admin POST forms currently do not include CSRF tokens. For production, CSRF protection should be added for cookie-authenticated form submissions.

## What I would improve next

- Add automated tests for auth, job CRUD, applications, and Redis behavior
- Add pagination UI controls
- Add CSRF protection for admin form posts
- Add Alembic migrations for production schema evolution
- Add richer email templates and background job delivery
- Add search/filtering for jobs and applications
- Improve admin UX with flash messages and validation summaries
