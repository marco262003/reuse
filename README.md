# REUSE – user side (Django + HTML/CSS/JS)

Frontend pages are plain HTML templates + CSS + vanilla JavaScript. The JavaScript talks to a
JSON API built with Django views. The same `core/services.py` rules power the user pages **and**
the Django admin site, so there is only one inventory and one set of transaction statuses.

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # set PostgreSQL password in .env

python manage.py check
python manage.py migrate            # creates application tables
python manage.py test
python manage.py create_reuse_admin # creates admin@reuse.com / admin123 only if absent
python manage.py runserver
```

Open http://127.0.0.1:8000/ . The custom staff dashboard is at `/admin-dashboard/`; the Django built-in admin is also available at `/admin/`.
Without SMTP configuration, verification links and reset codes print in the terminal running `runserver`. For PostgreSQL setup commands, follow `POSTGRES_SETUP.md`. Demo sample records are optional and can be created with `python manage.py seed_demo`.

## Try the whole flow

1. Register a community user and verify the email link printed in the development terminal (or delivered through configured SMTP). Alternatively, run `python manage.py seed_demo` to add sample data.
2. Log in as the community user → Browse Items → request an item (the "Wireless Presenter" is under repair: try **Notify When Available**).
3. Log in as the administrator → the app opens the custom `/admin-dashboard/` dashboard. Open **Borrowing Requests** and approve the request.
   The user now sees *Approved / Reserved*, the available quantity drops, and a notification appears.
4. On the approved request, choose **Confirm Pickup** → a Borrowing ID is created; it shows under *Borrowed Items* and *Items to Return*.
5. Open **Return Management** → choose the returned item's condition → **Confirm**. Stock is restored and the user is notified.
6. On **Manage Items**, adjust the units under repair to 0 → subscribed users receive one availability notification. Pending requests can be rejected by entering a rejection reason.

## Folder map

```
manage.py
reuse_project/        settings.py, urls.py, wsgi.py
core/
  models.py           User, Item, BorrowRequest, Borrowing, Notification, Delivery, AuditLog, ...
  services.py         ALL inventory + transaction rules (create/edit/cancel/approve/pickup/return)
  notifications.py    web + email + SMS delivery, tracked per channel (no duplicates)
  sms.py              SMS adapter (Semaphore example)
  validators.py       email domain, name, +63 mobile, password rules
  api_auth.py         register, verify email, login/logout, password reset, profile password
  api_data.py         items, requests, borrowings, returns, receipts, notifications, dashboard
  views_pages.py      the HTML pages            urls.py   all routes
  admin.py            admin actions (approve, reject, pickup, return, deactivate account)
  management/commands/process_due_notifications.py   due-soon / overdue reminders
  management/commands/seed_demo.py
  tests.py
templates/            base_auth, base_app, one page per screen, partials/
static/css/style.css
static/js/            api.js, common.js, items-ui.js (pop-ups), auth.js, dashboard.js, browse.js,
                      requests.js, borrowed.js, returns.js, profile.js, notifications.js
```

## Admin interface

Staff administrators who sign in through the main login page are sent to the custom `/admin-dashboard/` interface. It includes the dashboard, inventory management, request approval/rejection and pickup, return confirmation, user activation/deactivation, and system alerts. `/admin/` remains available for Django's built-in admin tools.

## Before real users touch it

- **Reminders:** schedule `python manage.py process_due_notifications` every 10–15 minutes
  (cron on Linux, Task Scheduler on Windows). It creates *due soon* / *overdue* notifications once each and retries failed email/SMS.
- **Database:** use PostgreSQL (`DB_ENGINE=postgres`, plus `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`).
  The code locks inventory rows while reserving, but SQLite ignores row locks.
- **Environment variables:** `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`, `SITE_URL` (used in verification links),
  `EMAIL_HOST` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` for real email, `SMS_API_KEY` for SMS.
  Without `SMS_API_KEY`, SMS deliveries are recorded as *skipped* – never as sent.
- **HTTPS:** with `DJANGO_DEBUG=0` the session and CSRF cookies become HTTPS-only.
- **Cache:** login throttling uses Django's cache. With several server processes, switch `CACHES` to Redis/Memcached.
- **Static files:** run `python manage.py collectstatic` and serve them with WhiteNoise or your web server.

## Choices I made where the mockup and requirements differ

- The request form uses the **Reason for Borrowing** dropdown from the requirements (with a required text box for *Other*) instead of the mockup's single "Purpose" box; the optional box is *Additional details*.
- Items to Return: the mockup labels an item due in 10 days as *Due Soon*, so every outstanding item that is not overdue shows *Due Soon*.
  The *due soon* **notification** is sent when the deadline is within `DUE_SOON_HOURS` (24, in `settings.py`).
- The mockup's **Borrowed Items** page lists a *Canceled* card in history. Canceled reservations (approved, then canceled before pickup) appear there.
- Login and reset messages match the requirements word for word, which tells a visitor whether an email is registered.
  I added attempt limits (10 failed logins per 15 minutes, 5 code guesses, 60-second resend, 5 codes per hour) to limit abuse.
- Mobile numbers are stored as `+639XXXXXXXXX` and must be unique (needed so a reset by number finds one account).
- Full name needs at least a first and last name; the middle name is optional.
- Availability is checked against the **current** available quantity (as in the requirements), not against overlapping future dates.
- Profile pictures show a default avatar; uploading photos is not built.

## Verification performed during packaging

The available test suite was run against SQLite in the packaging environment. PostgreSQL was not available in that environment, so configure PostgreSQL locally using `POSTGRES_SETUP.md` and run `python manage.py check` and `python manage.py test` there before using real accounts.
## PostgreSQL and image uploads

PostgreSQL is the default database backend. Set `DB_ENGINE=postgres` in `.env`; the app does not silently fall back to SQLite. `DB_ENGINE=sqlite` is only for isolated tests. Run migrations after configuring PostgreSQL.

Admins can upload item photos in **Manage Items** when creating or editing an item. Users can upload a profile photo from **My Profile**. Accepted formats are JPG, PNG, and WEBP, up to 5 MB. Development uploads are stored in `media/items/` and `media/profiles/`; Django serves these files only while `DEBUG=1`. For production, configure persistent media storage and serve media securely.
