# REUSE PostgreSQL setup on macOS

These steps assume PostgreSQL is installed and its server is running. Django migrations create the application's tables; you do **not** need to write table SQL by hand.

## 1. Open Terminal and check PostgreSQL

```bash
psql --version
pg_isready
```

If `pg_isready` says no response, start PostgreSQL using the app/service you installed it with before continuing.

## 2. Create the database and role

Connect as a PostgreSQL administrator. Depending on your installation, one of these may work:

```bash
psql postgres
```

At the `postgres=#` prompt, run the following SQL. Replace the example password with your own strong password. If the database or role already exists, don't repeat the corresponding `CREATE` statement; use `\du` and `\l` to inspect existing objects.

```sql
CREATE ROLE reuse WITH LOGIN PASSWORD 'replace-with-a-strong-password';
CREATE DATABASE reuse OWNER reuse;
\q
```

Test the account:

```bash
psql -h localhost -U reuse -d reuse
```

Enter the password you chose. If you reach the `reuse=>` prompt, enter `\q` to exit.

## 3. Set up the project

In Terminal, move into the folder containing `manage.py`:

```bash
cd /path/to/reuse
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `DB_PASSWORD` to the password you used above. Keep `DB_ENGINE=postgres`. Do not share `.env` or commit it to Git.

## 4. Create tables and administrator

```bash
python manage.py check
python manage.py migrate
python manage.py create_reuse_admin
python manage.py test
```

The initial development administrator is `admin@reuse.com` / `admin123` unless you changed `REUSE_ADMIN_EMAIL` and `REUSE_ADMIN_PASSWORD` in `.env` before running the command. The command is idempotent: if the account already exists, it will not reset its password. Change the development password before using real accounts.

## 5. Run REUSE

```bash
python manage.py runserver
```

Open <http://127.0.0.1:8000/>. The login page sends staff administrators to the custom `/admin-dashboard/` dashboard and community users to `/dashboard/`.

## Email and reminders

Without SMTP settings, verification/reset emails are printed to the terminal and are not actually delivered to a mailbox. Configure `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, and `DEFAULT_FROM_EMAIL` in `.env` to send real email. Set `SITE_URL` to the site's actual base URL for email links.

Run reminders manually during development with:

```bash
python manage.py process_due_notifications
```

For automatic reminders, schedule this command every 10–15 minutes using `cron` or a scheduler. SMS remains skipped unless a compatible SMS provider and key are configured; do not treat a skipped delivery as a sent message.

## Troubleshooting

- **`connection refused`**: start PostgreSQL and re-check with `pg_isready`.
- **`password authentication failed`**: verify `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT` in `.env`.
- **`database "reuse" does not exist`**: create it using step 2.
- **`No module named psycopg`**: activate `.venv` and run `pip install -r requirements.txt` again.
- **Changes to models**: run `python manage.py makemigrations` and then `python manage.py migrate`.

## Item and profile photos

- Admin item photos are uploaded from **Manage Items** when creating or editing an item.
- Community users upload a profile photo from **My Profile**.
- Supported formats: JPG, PNG, WEBP; maximum 5 MB per image.
- During local development (`DEBUG=1`), Django serves uploaded media from `media/`. Keep this folder and its contents when backing up the project. Do not store private uploads in a public static folder.
- For deployment, configure persistent media storage and a production web server or object storage to serve uploaded images; Django's development media serving is only enabled when `DEBUG=1`.
