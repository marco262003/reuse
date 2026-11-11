# REUSE – user side (Django + HTML/CSS/JS)

Frontend pages are plain HTML templates + CSS + vanilla JavaScript. The JavaScript talks to a
JSON API built with Django views. The same `core/services.py` rules power the user pages **and**
the Django admin site, so there is only one inventory and one set of transaction statuses.

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt # on a production server: requirements.txt
cp .env.example .env               # set PostgreSQL password in .env

python manage.py check
python manage.py migrate            # creates application tables
python manage.py fetch_face_models  # downloads the two face model files (about 39 MB), once
python manage.py test
python manage.py create_reuse_admin # creates admin@reuse.com / admin123 only if absent
python manage.py runserver
```

Open http://127.0.0.1:8000/ . The custom staff dashboard is at `/admin-dashboard/`; the Django built-in admin is also available at `/admin/`.
Without SMTP configuration, verification links, reset codes and sign-in codes print in the terminal running `runserver`. For PostgreSQL setup commands, follow `POSTGRES_SETUP.md`. Demo sample records are optional and can be created with `python manage.py seed_demo`.

`runserver` is the only command you need. During development it also holds uploaded ID photos itself, in memory, so there is no second server to start.

## Try the whole flow

1. Register a community user: upload a photo of a school ID, check the details read from it, fill in the rest, review, and create the account. Then open the verification link printed in the development terminal (or delivered through configured SMTP). Alternatively, run `python manage.py seed_demo` to add sample data; the demo account is asked for a school ID the first time it logs in.
2. Log in as the community user: email and password, then the face scan (look straight, turn your head as asked). Then Browse Items → request an item (the "Wireless Presenter" is under repair: try **Notify When Available**).
3. Log in as the administrator → the app opens the custom `/admin-dashboard/` dashboard. Open **Borrowing Requests** and approve the request.
   The user now sees *Approved / Reserved*, the available quantity drops, and a notification appears.
4. On the approved request, choose **Confirm Pickup** → a Borrowing ID is created; it shows under the *Active* tab of *Borrowed Items*.
5. Open **Return Management** → choose the returned item's condition → **Confirm**. Stock is restored and the user is notified.
6. On **Manage Items**, adjust the units under repair to 0 → subscribed users receive one availability notification. Pending requests can be rejected by entering a rejection reason.

## Folder map

```
manage.py
reuse_project/        settings.py, urls.py, wsgi.py
                      registration_config.py   every tunable value for ID sign-up and face login
core/
  models.py           User, Item, BorrowRequest, Borrowing, Notification, Delivery, AuditLog, ...
  services.py         ALL inventory + transaction rules (create/edit/cancel/approve/pickup/return)
  notifications.py    web + email + SMS delivery, tracked per channel (no duplicates)
  sms.py              SMS adapter (Semaphore example)
  registration.py     ID-first sign-up rules (drafts, reading the ID, duplicates, creating the account)
  face_login.py       pending sign-in, face scan, email-code fallback
  names.py            name parsing and name rules (pure Python)
  registration_fields.py  the one list that says which fields come from the ID and which are typed
  idscan/             swappable services: storage.py (object storage), preprocess.py (file reading, crop,
                      deskew, contrast), ocr.py, mapping.py (card template -> fields), faces.py, liveness.py
  api_registration.py schema, upload-id, extract-id
  validators.py       email domain, +63 mobile, password rules
  api_auth.py         register, verify email, login, face-scan, face-verify, enroll-id, login-code,
                      logout, password reset, profile password
  api_data.py         items, requests, borrowings, returns, receipts, notifications, dashboard
  views_pages.py      the HTML pages            urls.py   all routes
  views_admin.py      the staff dashboard pages
  admin.py            admin actions (approve, reject, pickup, return, deactivate account)
  management/commands/process_due_notifications.py   due-soon / overdue reminders, removes unfinished sign-ups
  management/commands/fetch_face_models.py           downloads the face model files
  management/commands/seed_demo.py, create_reuse_admin.py
  tests.py, tests_names.py, tests_registration.py, tests_face_login.py, tests_idscan.py
  testing.py          stand-ins for storage, OCR and the face engine, used by the tests
  idscan/local_storage.py  in-memory stand-in for object storage, started by runserver (development)
templates/            base_auth, base_app, one page per screen, partials/, admin/
static/css/style.css (single stylesheet for auth, user and admin pages)
static/js/            api.js, common.js, items-ui.js (pop-ups), auth.js, dashboard.js, browse.js,
                      requests.js, borrowed.js, profile.js, notifications.js,
                      register.js, autofill.js, id_upload.js (sign-up), login.js, face_scan.js (log in)
jstests/              tests for autofill.js (node --test jstests/)
idscan_models/        the downloaded face model files (not in git)
docs/                 REGISTRATION_AND_FACE_LOGIN.md: design, API, limits
```

## Admin interface

Staff administrators who sign in through the main login page are sent to the custom `/admin-dashboard/` interface. It includes the dashboard, inventory management, request approval/rejection and pickup, return confirmation, user activation/deactivation, and system alerts. `/admin/` remains available for Django's built-in admin tools.

## Registration with a school ID, and face login

**Read the known limits in `docs/REGISTRATION_AND_FACE_LOGIN.md` before relying on this.** It has not been tried on real school ID cards or with a real person in front of a camera.

### How it works

- **Sign-up** has three steps. First a photo of the school ID (JPG, PNG or PDF; the back is optional). REUSE reads the text on it, fills in only the fields that come from the ID, and makes a face template from the ID portrait. The person then types the rest (school email, mobile number, user type, password), reviews everything, and creates the account. Every field stays editable, and what the person typed always wins over what was read.
- **Log in** is email and password, then a face scan. The camera step asks the person to look straight and turn their head, which a still photo cannot do. The live face is matched against the template on the server.
- **When the face scan can't be used:** after 3 failed scans, or when the camera doesn't work, a one-time code is sent to the school email. The account owner gets a notification whenever a code was used instead of a face scan.
- **What is stored:** an encrypted face template (a list of numbers, not a photo), the details the person confirmed, and a log of checks. The ID image is deleted once the account exists. Camera pictures are never stored.

### The services, and where the files go

OCR and face matching run inside Django, so there is nothing extra to start for them. The first ID read after the server starts takes a few seconds longer while the models load.

Uploaded ID photos go straight from the browser to object storage. During development `runserver` starts an in-memory stand-in for it by itself (on port 9000, inside the same process) the first time an ID is uploaded; it forgets the files when the server stops or reloads. It never runs with `DEBUG` off or when `ID_BUCKET` is set. For real storage, set these in `.env`:

```
ID_BUCKET=your-bucket-name
S3_ENDPOINT_URL=            # empty for Amazon S3; the service's address for any other S3-compatible storage
AWS_REGION=ap-southeast-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
FACE_TEMPLATE_KEY=...       # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The bucket must block all public access and have a lifecycle rule that deletes everything under `pending/` after one day. The access key only needs to get, put and delete objects in that bucket. Real storage has only been exercised through the local stand-in and stubbed API calls, not against a real bucket.

### Settings

Every tunable value is in `reuse_project/registration_config.py`: the face-match threshold, how many scans may fail, allowed file types and sizes, how long sessions and sign-ups last, the liveness thresholds, and the school ID template with its name format. REUSE uses server-side sessions in an HttpOnly cookie, so the session length there stands in for a token expiry.

### Swapping the OCR or face engine

Each engine is a small class named in the settings:

- **OCR:** a class with a `name` and `read(image)` that returns `Line(text, confidence, box)` objects, top to bottom. Point `OCR_PROVIDER` at it. See `RapidOCRProvider` in `core/idscan/ocr.py`.
- **Faces:** a class with a `name`, `detect(image)` that returns `Face` objects (largest first) and `template(image, face)` that returns a unit-length list of numbers. Point `FACE_PROVIDER` at it. See `OpenCVFaceProvider` in `core/idscan/faces.py`.

Stored templates only work with the engine that made them. After changing `FACE_PROVIDER`, each person is asked for their school ID again at their next login. A new engine needs its own `FACE_MATCH_THRESHOLD`.

To fit another school's card, add an entry to `ID_TEMPLATES` with its name format (`comma`, `no_comma` or `auto`), the pattern of its ID number, and the label words printed on it.

### Accounts that have no face on file

Accounts created before this feature, accounts an administrator adds, and the `seed_demo` account are asked for a school ID the first time they log in, then do the face scan. Administrator accounts created from the command line skip the face scan unless `FACE_LOGIN_EXEMPT_SUPERUSERS` is turned off. An administrator can reset someone's face login from **User Management → Manage**, which makes them add their ID again.

### Tests

```bash
python manage.py test        # 142 tests
node --test jstests/         # 8 tests for the autofill rules (needs Node.js; optional)
```

The tests replace storage, OCR and the face engine with stand-ins, so they run without the model files or a network. They show that the rules work; they don't show that real cards are read or that real faces match.

## Before real users touch it

- **Face login and ID reading:** try them on real school IDs and real cameras first. Adjust `FACE_MATCH_THRESHOLD` and the ID template in `registration_config.py` to what you see. Until then, nobody knows how often the right person is rejected.
- **Camera needs HTTPS:** browsers only allow the camera on HTTPS pages (or on `localhost`).
- **Storage and keys:** use a private bucket with the lifecycle rule described above, and set `FACE_TEMPLATE_KEY`. Changing that key, or `DJANGO_SECRET_KEY` when no key is set, makes everyone add their ID again.
- **Server size:** the OCR and face models need a server that keeps a process running with about 2 GB of memory. Reading one ID took 3 to 10 seconds on a 4-core PC.
- **Rate limiting:** the ID upload and reading endpoints are open to visitors, because nobody has an account yet when they sign up. They are limited per sign-up and per network address, but put a rate limiter or web application firewall in front for a public site.
- **Privacy:** face templates and school IDs are sensitive personal data under the Data Privacy Act of 2012 (RA 10173). Have your data protection officer review this before real users register.
- **Reminders:** schedule `python manage.py process_due_notifications` every 10–15 minutes
  (cron on Linux, Task Scheduler on Windows). It creates *due soon* / *overdue* notifications once each, retries failed email/SMS, and removes sign-ups that were never finished.
- **Database:** use PostgreSQL (`DB_ENGINE=postgres`, plus `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`).
  The code locks inventory rows while reserving, but SQLite ignores row locks.
- **Environment variables:** `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS`, `SITE_URL` (used in verification links),
  `EMAIL_HOST` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` for real email, `SMS_API_KEY` for SMS.
  Without `SMS_API_KEY`, SMS deliveries are recorded as *skipped* – never as sent.
- **HTTPS:** with `DJANGO_DEBUG=0` the session and CSRF cookies become HTTPS-only.
- **Cache:** login throttling and the ID-reading limits use Django's cache. With several server processes, switch `CACHES` to Redis/Memcached.
- **Static files:** run `python manage.py collectstatic` and serve them with WhiteNoise or your web server.

## Choices I made where the mockup and requirements differ

- The request form uses the **Reason for Borrowing** dropdown from the requirements (with a required text box for *Other*) instead of the mockup's single "Purpose" box; the optional box is *Additional details*.
- The mockup's separate Items to Return page is merged into **Borrowed Items** because both listed the same items; `/returns/` redirects there.
  Active items show the time left, highlighted once the deadline is within `DUE_SOON_HOURS` (24, in `settings.py`), the same window as the *due soon* **notification**.
- The mockup's **Borrowed Items** page lists a *Canceled* card in history. Canceled reservations (approved, then canceled before pickup) appear in the *History* tab.
- The login error is the same for an unknown email and a wrong password, so it doesn't reveal whether an email is registered. The password reset message still does, as the original requirements asked.
  I added attempt limits (10 failed logins per 15 minutes, 5 code guesses, 60-second resend, 5 codes per hour) to limit abuse.
- Mobile numbers are stored as `+639XXXXXXXXX` and must be unique (needed so a reset by number finds one account).
- Names are stored as separate first, middle, last and suffix fields. First and last are required; the middle name and suffix are optional.
- Availability is checked against the **current** available quantity (as in the requirements), not against overlapping future dates.
- Profile pictures show a default avatar; uploading photos is not built.

## Verification performed during packaging

The available test suite was run against SQLite in the packaging environment. PostgreSQL was not available in that environment, so configure PostgreSQL locally using `POSTGRES_SETUP.md` and run `python manage.py check` and `python manage.py test` there before using real accounts.
## PostgreSQL and image uploads

PostgreSQL is the default database backend. Set `DB_ENGINE=postgres` in `.env`; the app does not silently fall back to SQLite. `DB_ENGINE=sqlite` is only for isolated tests. Run migrations after configuring PostgreSQL.

Admins can upload item photos in **Manage Items** when creating or editing an item. Users can upload a profile photo from **My Profile**. Accepted formats are JPG, PNG, and WEBP, up to 5 MB. Development uploads are stored in `media/items/` and `media/profiles/`; Django serves these files only while `DEBUG=1`. For production, configure persistent media storage and serve media securely.
