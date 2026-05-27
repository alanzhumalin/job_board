# Job Board MVP

## What was built

A lean full-stack job board built with one FastAPI application serving both server-rendered HTML pages and JSON API endpoints. It includes a public open-roles application flow, a protected admin area for managing listings and applications, email confirmations, and a Telegram admin bot bonus.

## Features

- Public HTML pages:
  - `GET /` — public homepage with open roles only
  - `GET /jobs` — redirects to `/`
  - `GET /jobs/{job_id}` — open job detail page
  - `POST /jobs/{job_id}/apply` — submit an application
  - success page after application submission
- Admin HTML pages:
  - login/logout through `/login`
  - protected dashboard at `/admin`
  - create, edit, open/close, and delete jobs
  - view all applications
  - view applications for a single job
  - connect a Telegram account from the dashboard
- JSON API:
  - public open jobs listing and detail
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
  - connected admins can manage jobs and review applications from Telegram

## Tech stack

- Python 3.11+
- FastAPI
- Jinja2 templates
- SQLAlchemy 2.0 async ORM
- PostgreSQL via `DATABASE_URL`
- Redis asyncio client via `REDIS_URL`
- PyJWT for admin authentication
- `pydantic-settings` for configuration
- Brevo Transactional Email API via `httpx`
- Telegram Bot API via `httpx`
- Railway deployment without Docker

## PostgreSQL usage

PostgreSQL is the primary application database.

- `jobs` table stores job postings
- `applications` table stores submitted applications
- `telegram_admins` / `TelegramAdmin` stores Telegram users connected through the admin deep-link flow
- `applications.job_id` references `jobs.id`
- unique constraint on `(job_id, email)` prevents duplicate applications to the same role from the same email
- emails are normalized to lowercase before insert
- tables are created on FastAPI startup using `Base.metadata.create_all()`

## Redis usage

Redis is used for runtime caching, short-lived locks, and temporary Telegram bot state.

1. Public open jobs cache
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

3. Telegram deep-link bind token
- temporary key format:
  - `telegram:bind:{token}`
- used when an authenticated admin clicks `Connect Telegram`
- token expires after a short TTL and is deleted after successful connection

4. Telegram interactive create-job state
- temporary key format:
  - `telegram:create_job:{telegram_user_id}`
- stores the current step and answers during `/create`
- TTL is `30` minutes

5. Telegram delete confirmation state
- temporary key format:
  - `telegram:delete:{telegram_user_id}:{job_id}`
- used before destructive `/delete` actions
- TTL is `5` minutes

## Admin auth explanation

- Admin credentials come from environment variables:
  - `ADMIN_USERNAME`
  - `ADMIN_PASSWORD`
- Login is exposed at `/login`
- Login creates a JWT using `JWT_SECRET`
- For browser admin pages, the JWT is stored in an `HttpOnly` cookie
- Protected admin browser routes use the `HttpOnly` cookie
- Protected admin JSON endpoints can also be tested with `Authorization: Bearer <token>` where implemented
- Password comparison uses `hmac.compare_digest`
- There is no public admin registration by design

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
- no second worker process is required
- on startup, if configured, the app attempts to register the Telegram webhook automatically
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
5. Attach the Railway PostgreSQL `DATABASE_URL` and Railway Redis `REDIS_URL` service variables to the FastAPI web service.
6. Set environment variables in the web service:
   - `APP_ENV=production`
   - `DATABASE_URL`
   - `REDIS_URL`
   - `ADMIN_USERNAME`
   - `ADMIN_PASSWORD`
   - `JWT_SECRET`
   - `JWT_EXPIRE_HOURS`
   - `APP_BASE_URL` set to your Railway domain
   - Brevo values for real email confirmation:
     - `BREVO_API_KEY`
     - `BREVO_FROM_EMAIL`
     - `BREVO_FROM_NAME`
   - optional SMTP fallback values:
     - `SMTP_HOST`
     - `SMTP_PORT`
     - `SMTP_USERNAME`
     - `SMTP_PASSWORD`
     - `SMTP_FROM_EMAIL`
   - optional Telegram bot values for the bonus:
     - `TELEGRAM_BOT_TOKEN`
     - `TELEGRAM_BOT_USERNAME`
     - `TELEGRAM_WEBHOOK_SECRET`
7. Railway will install dependencies from `requirements.txt`.
8. `railway.json` starts the app with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

No `Dockerfile` or `docker-compose.yml` is required.

## Telegram setup with BotFather

1. Create a bot with BotFather and copy the bot token.
2. Note the bot username from BotFather.
3. Set these Railway variables:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_BOT_USERNAME`
   - `TELEGRAM_WEBHOOK_SECRET`
   - `APP_BASE_URL`
4. Deploy or restart the Railway app.
5. Log in to the admin dashboard and click `Connect Telegram`.
6. Press `Start` in the Telegram chat that opens.
7. Send `/help` to confirm the bot is connected.

## Live URLs

- GitHub repo: `https://github.com/alanzhumalin/job_board`
- Live frontend: `https://jobboard-production-e62e.up.railway.app/`
- Backend `/docs` URL: `https://jobboard-production-e62e.up.railway.app/docs`
- Admin login: `https://jobboard-production-e62e.up.railway.app/login`

## Submission checklist

- GitHub repo URL: `https://github.com/alanzhumalin/job_board`
- Live frontend URL: `https://jobboard-production-e62e.up.railway.app/`
- Backend `/docs` URL: `https://jobboard-production-e62e.up.railway.app/docs`
- Railway dashboard screenshot showing the FastAPI app, PostgreSQL, and Redis services

## Known limitations

- Admin POST forms currently do not include CSRF tokens. For production, CSRF protection should be added for cookie-authenticated form submissions.
- Database schema creation uses `Base.metadata.create_all()` for speed in the take-home exercise. In production, this should be replaced with Alembic migrations.
- Email sending is synchronous/best-effort from the request flow. In production, I would move it to a background queue.

## What I would improve next

- Add automated tests for auth, job CRUD, applications, Redis behavior, and Telegram commands
- Add pagination UI controls
- Add CSRF protection for admin form posts
- Add Alembic migrations for production schema evolution
- Add a background worker for email delivery
- Add richer email templates and a verified custom sending domain
- Add search/filtering for jobs and applications
- Improve admin UX with flash messages and validation summaries
- Add inline keyboards/buttons for the Telegram bot
