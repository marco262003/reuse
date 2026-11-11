# ID-first registration and face login

**Status: built on 2026-11-10.** This document describes the system as it is in the code. How to run it is in the README.

**Before relying on it, read [What was checked and what was not](#9-what-was-checked-and-what-was-not) and [Known limits](#10-known-limits).** It has not been tried on real school ID cards or with a real person in front of a camera.

## 1. Decisions

| # | Question | Answer | Where the answer comes from |
| --- | --- | --- | --- |
| 1 | Tech stack | Built into REUSE: Django 5.2, HTML templates and vanilla JavaScript. OCR and face matching run as Python modules inside the Django process. | Your choice |
| 2 | Database | PostgreSQL, as before. | The project |
| 3 | Hosting | Not decided. It works on any host that keeps a Python process running with about 2 GB of RAM (not serverless). ID photos go to any S3-compatible storage set in `.env`; development uses a local stand-in. | My assumption |
| 4 | ID template | One school. The name is printed as `SURNAME, First Middle`. | Your choice |
| 5 | User types | Student, faculty, staff. Administrators are separate accounts and don't self-register. | The project |
| 6 | False accept vs false reject | The face model's published threshold (cosine similarity 0.363), leaning towards fewer false rejections because the password is checked first and there is an email-code fallback. | My assumption |
| 7 | After failed face scans | 3 failed scans, then a one-time code sent to the school email. | Your choice |

### Technology

| Part | What is used | Why |
| --- | --- | --- |
| OCR | RapidOCR 3.10 (PaddleOCR's PP-OCR text models, run by ONNX Runtime; the models ship inside the package) | Installs with pip on Python 3.14 for Windows. PaddleOCR itself can't: PaddlePaddle has no build for it. Tesseract needs a separate installer and reads ID cards less well. |
| Face detection and matching | OpenCV 5 with its YuNet detector and SFace recognizer (a 128-number face template), compared by cosine similarity | Needs only OpenCV, runs on CPU, permissive licences. `face_recognition` needs a C++ compiler, and DeepFace needs TensorFlow, which has no Python 3.14 build for Windows. |
| Liveness | A random head-turn challenge over a burst of camera frames, checked on the server | Lightweight and open. Its limits and the commercial alternative are in section 10. |
| PDF pages to images | pypdfium2 | Permissive licence, ready-made build. |
| Password hashing | Argon2id (`argon2-cffi`) | Named in the spec. Older PBKDF2 hashes still work and are re-hashed at each person's next login. |
| Sessions | Django server-side sessions in an HttpOnly cookie | The spec allows this in place of JWTs, and REUSE already worked this way. |
| Object storage | `boto3` against any S3-compatible endpoint, with pre-signed POST uploads | A pre-signed POST lets the storage service itself enforce file size and type. |
| Face template encryption | Fernet (`cryptography`), key from `.env` | Keeps templates unreadable in database dumps. |

## 2. Architecture

```
┌────────────────────────────── Browser ──────────────────────────────┐
│ Sign up:  1 upload school ID → 2 details (autofilled) → 3 review    │
│           register.js · autofill.js · id_upload.js                  │
│ Log in:   1 email + password → 2 face scan   login.js · face_scan.js│
└───────────┬────────────────────────────────────────┬────────────────┘
            │ JSON, session cookie, CSRF token       │ ID file only: direct upload
            ▼                                        ▼ with a pre-signed POST
┌──────────────────── Django (REUSE) ───────────────────┐  ┌─────────────────────────┐
│ API                                                   │  │ Object storage          │
│   api_registration.py   schema, upload-id, extract-id │  │ (private, S3-compatible)│
│   api_auth.py           register, login, face-scan,   │  │                         │
│                         face-verify, enroll-id,       │  │ pending/{draft}/id/...  │
│                         login-code                    │  │                         │
│ Rules                                                 │◄─┤ Django reads the file   │
│   registration.py       drafts, reading, duplicates   │  │ into memory, never to   │
│   face_login.py         challenge, attempts, fallback │  │ its own disk, and       │
│ Pure Python (no Django)                               │  │ deletes it once the     │
│   names.py              name parsing                  │  │ account exists.         │
│   registration_fields.py  ID-derived vs manual fields │  └─────────────────────────┘
│ Services (swappable)   core/idscan/                   │
│   storage.py     pre-signed POST, fetch, move, delete │
│   preprocess.py  file reading, crop, deskew, contrast │
│   ocr.py         OCR engine      → RapidOCR           │
│   mapping.py     card template   → fields             │
│   faces.py       face engine     → YuNet + SFace      │
│   liveness.py    head-turn challenge                  │
└──────────────────────────┬────────────────────────────┘
                           ▼
┌────────────────────── PostgreSQL ─────────────────────┐
│ users (name parts, ID fields, encrypted face template)│
│ ID document metadata · verification records · drafts  │
└───────────────────────────────────────────────────────┘
```

The OCR and face services run inside Django, so there is nothing extra to start for them. Each loads its models once per server process. The interfaces are narrow enough to move them into their own process later.

### Registration flow

1. The first screen asks for the school ID: a JPG, PNG or PDF of the front, and optionally the back.
2. `upload-id` checks the declared type and size and returns a pre-signed POST. The browser sends the file straight to storage.
3. `extract-id` reads the file from storage into memory and then:
   - checks its size and its real type from the first bytes (the file signature), not its name; a PDF's first page is turned into an image;
   - hashes it and refuses an ID image that already belongs to an account;
   - tries the picture at each quarter turn and keeps the turn where the portrait is found most surely, so a sideways scan still works;
   - fails with "We couldn't read the photo on your ID. Please retake it in good light, without glare." when there is no usable portrait;
   - makes the face template from the portrait and keeps it, encrypted, on the registration draft;
   - crops, levels and boosts the contrast of the picture, runs OCR, maps the text to fields with the school's template, and parses the name.
4. The form opens with only ID-derived fields filled in, each marked "Read from your ID". Fields the reading was unsure about say "please check". Failed or partial OCR never blocks: empty fields are typed by hand.
5. The review screen shows ID-derived fields and typed fields in separate groups, with a badge on each ID-derived value: read from the ID, read and changed, or typed. **Edit Details** goes back to the form.
6. `register` validates every field, creates the account from exactly the values sent, moves the face template to the user, deletes the ID image, and sends the usual verification email.

Uploading a different ID after editing asks first: "This ID contains different information than what's on the form. Replace your edits, or keep them?" Keeping them still lets untouched autofilled fields follow the new ID, and clears values that came only from the old one.

### Login flow

1. `login` checks email and password. An unknown email and a wrong password get the same message, and the server spends the same hashing time on both.
2. Valid credentials don't log the person in. The session only records a pending sign-in, valid for 5 minutes.
3. `face-scan` returns a random challenge: look straight, turn, look straight, turn, with each turn left or right at random.
4. The browser shows each prompt, captures 4 frames for it, and sends them all to `face-verify`. The server checks, in order:
   - exactly one face in each usable frame, and a face for every step;
   - liveness: for each step, some frame shows the head straight or turned the right way far enough (measured by where the nose sits between the eyes);
   - one person throughout: the turned frames still look like the straight-on frame;
   - match: the straight-on frame against the stored template, by cosine similarity.
5. Pass: the session becomes a normal logged-in session. Fail: see section 6.

The stored template is never sent to the browser, and neither is the similarity score. Camera frames are processed in memory and never stored.

## 3. Database schema

### `core_user` (changed)

| Column | Type | Notes |
| --- | --- | --- |
| `first_name` | varchar(60) | Required |
| `middle_name` | varchar(60) | May be empty or a single letter |
| `last_name` | varchar(60) | Required; compound surnames stay whole |
| `suffix` | varchar(10) | Empty, or Jr, Sr, II, III, IV, V, or other short text |
| `student_id_number` | varchar(20), unique, null | One account per ID number |
| `date_of_birth` | date, null | |
| `program` | varchar(120) | |
| `year_level` | varchar(20) | `1` to `6` |
| `sex`, `address`, `college`, `id_valid_until` | varchar | Filled only if printed on the ID or typed |
| `face_template` | bytea, null | Encrypted face template. Not a photo. |
| `face_model` | varchar(40) | Which engine made the template |
| `face_enrolled_at` | timestamptz, null | |
| `consent_at` | timestamptz, null | When the person agreed to ID and face processing |

Removed in migration 0004:

- `full_name`. It is now a computed display name, and existing names were split into parts.
- The earlier verification columns: `identity_verification_status`, `identity_verified`, `identity_verified_at`, `identity_attempts_reset_at`, `reference_photo_key`, `reference_face_id`, `school_id_hash`, `school_id_last4`.
- The `core_faceverificationattempt` table.

### `core_registrationdraft` (new)

One row per sign-up in progress. Its id lives in the visitor's session and is what authorises the ID calls before an account exists.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | |
| `created_at`, `expires_at` | timestamptz | Expires after 30 minutes |
| `ip` | inet | |
| `uploads`, `extractions` | smallint | Counted against the limits in section 6 |
| `face_template`, `face_model` | bytea, varchar | Held here until `register` succeeds |

### `core_iddocument` (new: uploaded document metadata)

| Column | Type | Notes |
| --- | --- | --- |
| `id` | uuid | |
| `draft_id`, `user_id` | foreign keys, null | Draft first, user once registered |
| `side` | `front` or `back` | |
| `storage_key` | varchar(200) | `pending/{draft}/id/{side}-{random}.{ext}` |
| `content_type`, `size_bytes` | varchar, int | The real type and size, as found in the file |
| `sha256` | char(64) | Unique among registered front images |
| `raw_name` | text | The name exactly as OCR read it, for audit |
| `name_format` | `comma` or `no_comma` | |
| `field_confidence` | jsonb | Field name → confidence. No field values. |
| `face_found` | bool | |
| `ocr_engine` | varchar(40) | |
| `created_at`, `image_deleted_at` | timestamptz | |

### `core_verificationrecord` (new: audit log)

| Column | Type | Notes |
| --- | --- | --- |
| `user_id`, `draft_id` | null | Whichever applies |
| `kind` | `id_extract`, `face_login`, `email_code` | |
| `outcome` | `success`, `no_match`, `liveness_failed`, `no_face`, `multiple_faces`, `bad_file`, `duplicate`, `wrong_code`, `error` | |
| `similarity` | float, null | A score only. Never images or templates. |
| `ip`, `created_at` | inet, timestamptz | |

`core_passwordresetcode` gained a `purpose` column (`reset` or `login`). A sign-in code cannot reset a password, and a reset code cannot sign anyone in.

## 4. API

All endpoints answer in REUSE's usual shape: `{"ok": true, ...}` or `{"ok": false, "message": "...", "errors": {"field": "..."}}`, sometimes with a `code`. All POSTs need the CSRF token.

### `GET /api/registration/schema/`

The field list from the classification config.

```json
{"ok": true,
 "fields": [{"name": "last_name", "label": "Last name", "source": "id", "required": true, "required_for": [],
             "kind": "text", "max_length": 60, "choices": [], "autocomplete": "off"},
            {"name": "email", "label": "School email", "source": "manual", "required": true, "kind": "email"}],
 "upload": {"max_bytes": 8388608, "types": ["image/jpeg", "image/png", "application/pdf"]}}
```

### `POST /api/registration/upload-id/`

```json
{"side": "front", "content_type": "image/jpeg", "size": 2483921}
```

```json
{"ok": true, "document_id": "6f1c0e52-...",
 "upload": {"url": "https://<storage endpoint>/<bucket>",
            "fields": {"key": "pending/<draft>/id/front-<random>.jpg", "Content-Type": "image/jpeg", "policy": "...", "x-amz-signature": "..."},
            "expires_in": 300}}
```

The browser then POSTs `fields` plus the file to `url`. Uploading a side again replaces it; a new front also discards the old back.

Errors: `400` with `errors.file` or `errors.side`; `429` too many uploads; `503` storage not set up or unreachable.

### `POST /api/registration/extract-id/`

Request body `{}`: it works on the documents in the caller's draft.

```json
{"ok": true,
 "face": {"found": true},
 "name": {"format": "comma", "low_confidence": false},
 "fields": {"last_name": {"value": "Dela Cruz", "confidence": 0.8},
            "first_name": {"value": "Juan", "confidence": 0.8},
            "suffix": {"value": "Jr", "confidence": 0.76},
            "student_id_number": {"value": "21-12345", "confidence": 0.99}},
 "warnings": ["We couldn't read some details. Please check the form and type what's missing."]}
```

A field that wasn't read is left out, never sent as `""`, `null` or `"N/A"`. Only ID-derived fields can appear, whatever is printed on the card.

| Status | `code` | Meaning |
| --- | --- | --- |
| 422 | `no_face_on_id` | No usable portrait. Retake the photo. |
| 400 | `invalid_file` | Not a real JPG, PNG or PDF, or damaged, or too large |
| 409 | `duplicate_id` | This ID image is already registered |
| 404 | `upload_missing` | Nothing arrived in storage |
| 429 | | Too many tries |
| 503 | | Storage unreachable, or the face model files are missing |

### `POST /api/auth/register/`

```json
{"first_name": "Juan", "middle_name": "", "last_name": "Dela Cruz", "suffix": "Jr",
 "student_id_number": "21-12345", "date_of_birth": "2004-05-17", "program": "BS Information Technology", "year_level": "3",
 "email": "juan@g.batstate-u.edu.ph", "mobile": "+639171234567", "user_type": "student",
 "password": "...", "confirm_password": "...", "consent": true}
```

```json
{"ok": true, "email_sent": true, "message": "A verification link has been sent to your school email."}
```

Status `201`. Errors: `400` with per-field `errors`; `409` `no_reference_face` when the draft expired or has no portrait; `409` `duplicate_id`.

### `POST /api/auth/login/`

```json
{"email": "juan@g.batstate-u.edu.ph", "password": "..."}
```

| Response | Meaning |
| --- | --- |
| `200 {"ok": true, "next": "face_scan", "attempts_left": 3}` | Credentials valid; face scan required |
| `200 {"ok": true, "next": "enroll_id", ...}` | Valid, but the account has no usable face template: add a school ID first |
| `200 {"ok": true, "next": "email_code", "message": "..."}` | Valid, but face scan is paused; a code was emailed |
| `200 {"ok": true, "redirect": "/admin-dashboard/"}` | A command-line administrator without an ID |
| `401 {"message": "Email or password is incorrect."}` | Unknown email or wrong password, identical in both cases |
| `403` | Deactivated or email not verified; only shown after a correct password |
| `429` | 10 failed logins in 15 minutes |

### `POST /api/auth/face-scan/`

Request `{}`. Needs a pending sign-in; without one every face endpoint answers `401` with `"redirect": "/login/"`.

```json
{"ok": true, "challenge_id": "b2a9...", "steps": ["center", "left", "center", "right"],
 "frames_per_step": 4, "expires_in": 60, "attempts_left": 3}
```

Each challenge works once, only for its own id, and expires after 60 seconds.

### `POST /api/auth/face-verify/`

```json
{"challenge_id": "b2a9...",
 "frames": [{"step": 0, "image": "data:image/jpeg;base64,..."}, {"step": 1, "image": "..."}]}
```

| Response | Counts as a failed try? |
| --- | --- |
| `200 {"ok": true, "redirect": "/dashboard/"}` | |
| `401 {"code": "not_confirmed", "attempts_left": 2}` for a mismatch or failed liveness | Yes |
| `422 {"code": "no_face"}`: a step had no visible face | No |
| `422 {"code": "multiple_faces"}`: more than one person in view | No |
| `410 {"code": "challenge_expired"}`: ask for a new challenge | No |
| `423 {"code": "face_paused", "next": "email_code"}`: the third failure; a code was emailed | |

A mismatch and a failed liveness check return the same message, so nobody can learn which part to work on. The verification log records the real reason.

### `POST /api/auth/login-code/send/` and `/verify/`

`send` emails a 6-digit code to the account's school email. It is called automatically after the third failed scan, and the login page offers it when the camera can't be opened. `verify` takes `{"code": "123456"}` and answers like a successful `face-verify`.

Codes follow the password-reset rules: 10 minutes, 5 guesses, 60 seconds between sends, 5 per hour.

### `POST /api/auth/enroll-id/`

For a pending sign-in whose account has no usable face template. After `upload-id` and `extract-id`, `{"consent": true}` stores the template on the account. The person then does the face scan against it, which also shows the ID is theirs.

## 5. Field classification

One list, `FIELDS` in `core/registration_fields.py`, is the only place a field's category is set. The sign-up form is drawn from it, the autofill script only writes to fields it marks as ID-derived, and the server filters `extract-id` output through it.

| Field | Category | Required |
| --- | --- | --- |
| `last_name`, `first_name` | ID-derived | Yes |
| `middle_name`, `suffix` | ID-derived | No |
| `student_id_number` | ID-derived | Yes |
| `program`, `year_level` | ID-derived | Students only |
| `college`, `date_of_birth`, `sex`, `address`, `id_valid_until` | ID-derived | No |
| `email`, `mobile`, `user_type` | Manual only | Yes |
| `password`, `confirm_password` | Manual only, `autocomplete="new-password"` | Yes |
| `consent` | Manual only | Yes |

A field added later is manual unless it is declared ID-derived. Validation depends on the field, never on whether it was autofilled.

### Name parsing

`parse_name(raw, name_format, ocr_confidence)` in `core/names.py` is a pure function with no Django imports.

- Suffixes (`JR`, `JR.`, `SR`, `II`, `III`, `IV`) are taken out first, wherever they are, and returned separately.
- **Comma format:** left of the first comma is `last_name`; on the right, the first word is `first_name` and the rest is `middle_name`.
- **No comma:** first word, optional middle, and a surname that keeps its particles (`DELA CRUZ`, `DE LA SALLE`, `VAN DER BERG`). Marked low confidence.
- **Comma template but OCR lost the comma:** the surname is still taken from the front, and the result is marked low confidence.
- `N/A`, `NA`, `NONE`, `-`, `.` and stray symbols count as empty. A single letter is a valid middle initial.
- ALL-CAPS words become Title Case; words already in mixed case are kept as printed.
- Each part gets its own confidence. The raw OCR string goes to `core_iddocument.raw_name` only.
- Parts that were not found are left out, never returned as `""`.

## 6. Policies

**Duplicates.** A sign-up is refused if its ID number is already registered, or if its front ID image is byte-for-byte the same as a registered one. The person is told to log in or contact the administrator. The image check only catches the identical file; the ID number is the real guard.

**Failed face scans.** 3 failed scans for one account within 15 minutes, counted since the last successful scan, pause face scan for that account until those failures are 15 minutes old. Sign-in then needs the emailed code. Scans with no face or several faces don't count, up to 10 per sign-in.

**The email code.** It is also available on request, so that someone on a computer without a working camera can still log in. The account owner gets a web and email notification every time a code was used instead of a face scan.

**Retention.**

- ID images are deleted from storage as soon as the account exists (`ID_IMAGE_RETENTION_DAYS = 0`). A higher value keeps them under `users/{userId}/id/`, and a bucket lifecycle rule must then delete them.
- Unfinished sign-ups expire after 30 minutes. `process_due_notifications` removes them and their files; the bucket's lifecycle rule on `pending/` is the backstop.
- Camera frames are never stored.
- The face template and the verification log are deleted with the account.

**Sessions.** Login session: 8 hours, new session id at login, ended at logout, password reset or deactivation. Pending sign-in: 5 minutes. Registration draft: 30 minutes. Upload URL: 5 minutes. Challenge: 60 seconds. An expired session gets a `401` and the browser returns to the login page.

**Rate limits.** ID uploads: 12 per sign-up. ID reads: 10 per sign-up and 100 per hour per network address (generous, because a whole campus can share one address). Failed logins: 10 per 15 minutes. Face scans and codes: as above.

## 7. Configuration

All tunable values are in `reuse_project/registration_config.py`, loaded by `settings.py`. Secrets stay in `.env`.

| Setting | Default |
| --- | --- |
| `FACE_MATCH_THRESHOLD` | 0.363 (cosine similarity, SFace) |
| `FACE_MAX_FAILED_SCANS`, `FACE_PAUSE_MINUTES` | 3, 15 |
| `FACE_UNUSABLE_SCANS_PER_SIGN_IN` | 10 |
| `FACE_LOGIN_EXEMPT_SUPERUSERS` | True |
| `ID_FACE_MIN_PIXELS`, `SELFIE_FACE_MIN_SHARE` | 60 pixels wide; 15% of the frame's width |
| `LIVENESS_TURN_STEPS`, `LIVENESS_FRAMES_PER_STEP`, `LIVENESS_CHALLENGE_SECONDS` | 2, 4, 60 |
| `LIVENESS_TURN_MIN`, `LIVENESS_CENTER_MAX`, `LIVENESS_SAME_PERSON_MIN` | 0.14, 0.08, 0.30 |
| `ID_UPLOAD_TYPES`, `ID_UPLOAD_MAX_BYTES` | JPG, PNG, PDF; 8 MB |
| `ID_UPLOADS_PER_DRAFT`, `ID_EXTRACTS_PER_DRAFT`, `ID_EXTRACTS_PER_IP_PER_HOUR` | 12, 10, 100 |
| `ID_IMAGE_RETENTION_DAYS` | 0 |
| `ID_TEMPLATE`, `ID_TEMPLATES` | `batstateu`: name format `comma`, ID number pattern `NN-NNNNN`, label words |
| `OCR_PROVIDER`, `FACE_PROVIDER`, `FACE_MODEL_DIR` | RapidOCR; OpenCV YuNet + SFace; `idscan_models/` |
| `SESSION_COOKIE_AGE`, `PENDING_LOGIN_SECONDS`, `REGISTRATION_DRAFT_SECONDS`, `UPLOAD_URL_SECONDS` | 8 hours, 300, 1800, 300 |
| `.env`: `ID_BUCKET`, `S3_ENDPOINT_URL`, `AWS_REGION`, AWS keys, `FACE_TEMPLATE_KEY`, `FACE_MATCH_THRESHOLD`, `ID_TEMPLATE` | With `DEBUG` on and no `ID_BUCKET`, the local stand-in at `http://127.0.0.1:9000` is used, started automatically by `runserver` |

## 8. What changed in REUSE

- **Removed:** the Amazon-based verification: `core/face_aws.py`, `core/identity.py`, `core/api_identity.py`, `core/tests_identity.py`, the Verify Identity page and script, the reference-photo and approval controls on the admin user page, `deploy/identity-verification.yaml`, `docs/IDENTITY_VERIFICATION.md`.
- **Kept:** the admin pages to add, edit, deactivate and delete users.
- **Changed:**
  - the registration page is three steps;
  - the login page has the face-scan, email-code and add-your-ID steps;
  - the login error is one generic message;
  - names are stored in parts, and `full_name` is computed from them;
  - passwords are hashed with Argon2;
  - the admin user page shows face-login status, the verification log and a **Reset Face Login** button;
  - `process_due_notifications` also removes unfinished sign-ups.
- **New command:** `fetch_face_models`. Local storage for development starts inside `runserver`, so there is no extra command.
- **Migration 0004** makes all database changes. It keeps the spelling and capital letters of existing names when it splits them.

## 9. What was checked, and what was not

**Checked**

- 142 automated tests pass on SQLite and on PostgreSQL, and 8 tests of the autofill rules pass under Node.js. These replace storage, OCR and the face engine with stand-ins. Storage calls are also checked against Amazon's published S3 API.
- The real OCR and face engines, through the real endpoints, on a made-up ID card with a sample portrait:
  - a tilted photo and a sideways PDF were both read correctly (name parts, suffix, ID number, program, college, date of birth, validity);
  - the template made from the card matched the portrait itself at 0.96 to 0.97. This is the same photo on both sides, so it shows the parts are wired together correctly, not how accurate matching is;
  - a still photo of the right person was rejected by the liveness check;
  - two faces, no face, and a card with no portrait were each refused with the right message.
- In a browser, against a throwaway database with the real engines and the local storage stand-in: all three sign-up steps, the "replace or keep" prompt, account creation, email verification, login, three rejected scans of a still photo leading to the email code, the add-your-ID path, the no-camera path, and the admin pages. A passing face scan was walked through with the stand-in face engine.

**Not checked**

- Real school ID cards. Which labels and layout they use is a guess.
- A real person in front of a real camera. The head-turn thresholds have never seen a real head turn.
- How often a live face matches an ID portrait of the same person, and how often a different person passes.
- A real storage bucket. Only the local stand-in and stubbed API calls were used.
- A production deployment, HTTPS, or more than one server process.

## 10. Known limits

- **The reference is a small printed portrait.** Matching a live face to a photographed ID card rejects the right person more often than matching two selfies. The threshold is the model's published value and has not been tuned.
- **Liveness is basic.** The head-turn check stops a printed photo, a screenshot or a still image, because a flat picture can't move like a head. It does not stop a video of the person turning their head the right way, a face-swap tool, or software that feeds fake camera frames to the browser. The stronger option is a commercial service such as Amazon Rekognition Face Liveness, which costs money per check, sends face video to Amazon, and needs a React component and Amazon Cognito.
- **The email code is a second way in.** Anyone with the password and access to the school mailbox can log in without a face scan. That is no weaker than the existing password reset, which the same mailbox already controls, but it means the face scan is not the only gate.
- **Administrators created from the command line skip the face scan.** They are the most powerful accounts. Set `FACE_LOGIN_EXEMPT_SUPERUSERS = False` to make them add an ID.
- **OCR mapping is untuned.** Autofill may miss fields on real cards; the person simply types them.
- **Speed.** Reading one ID took 3 to 10 seconds on a 4-core PC, longest for PDFs. The first read after the server starts is slower while the models load.
- **The ID endpoints are open to visitors.** Nobody has an account yet when they sign up, so these calls are tied to a sign-up session and rate-limited, not logged in. A public site should also have a rate limiter in front.
- **The local storage stand-in doesn't enforce upload rules.** Real S3-compatible storage rejects wrong sizes and types itself; the stand-in accepts anything, so only the server-side checks are exercised locally.
- **Privacy.** Face templates and school IDs are sensitive personal data under the Data Privacy Act of 2012 (RA 10173). Have your data protection officer review this before real users register. This is not legal advice.

## 11. Decisions I made that you can overrule

1. Only names and the ID number are required of everyone; program and year level only of students; date of birth is optional.
2. ID images are deleted as soon as the account exists. Only a hash and metadata remain.
3. Command-line administrator accounts skip the face scan unless they add an ID.
4. The email code can be requested when the camera doesn't work, not only after 3 failed scans. Without that, nobody could log in on a computer with no webcam.
5. A mismatch and a failed liveness check look the same to the person logging in.
6. ALL-CAPS surname particles are capitalised as in the spec's examples ("Dela Cruz", "De La Salle", "Van Der Berg"), although its rule 3 says to keep particles lowercase. `bin` and `binti` stay lowercase inside a name, and mixed-case text is kept as printed.
7. `V` is not treated as a suffix when reading a card, because after a comma it is usually a middle initial. It can still be typed as a suffix.
8. Dots are dropped from names, so `MA. CRISTINA` is read as first name "Ma" and middle name "Cristina", and a typed "Ma." is refused. This follows the spec's "letters, spaces, hyphens and apostrophes only".
9. The spec treats `NA` as an empty value, so a person whose surname is "Na" cannot register without a change to that rule.
10. On a comma-format card where OCR loses the comma, the surname is still taken from the front. The spec only describes guessing first-middle-last.
11. Password reset still says whether an email or mobile number is registered, as the original requirements asked. The generic error covers login only.

## 12. What was installed

| What | Size | From |
| --- | --- | --- |
| `rapidocr` 3.10.0, `onnxruntime` 1.31.1, `opencv-python-headless` 5.0.0.93, `numpy` 2.5.4, `pypdfium2` 5.15.2, `cryptography` 50.0.2, `argon2-cffi` 25.1.0, `shapely` 2.2.0 and small dependencies | about 111 MB | PyPI |
| For local running and tests: `moto` 5.2.5, `flask` 3.1.3, `flask-cors` 6.0.5 and small dependencies | about 9 MB | PyPI |
| `face_recognition_sface_2021dec.onnx` (Apache-2.0) | 38.7 MB | OpenCV's model zoo, `github.com/opencv/opencv_zoo` |
| `face_detection_yunet_2023mar.onnx` (MIT) | 0.2 MB | same |

`fetch_face_models` downloads the two model files again when needed and checks them against the SHA-256 hashes of the files this was built with.

## 13. Tests

| Spec requirement | Where it is tested |
| --- | --- |
| Autofill writes only ID-derived fields; manual fields are never touched | `tests_registration.AutofillTests`, `tests_names.FieldClassificationTests`, `jstests/autofill.test.js` |
| Comma and no-comma name parsing, suffix extraction, compound surnames, missing middle name, middle initial | `tests_names` |
| Partial or failed OCR leaves blanks and still allows registration | `tests_registration.AutofillTests` |
| Editing one name part doesn't affect the others; the person's edits win | `jstests/autofill.test.js`, `tests_registration.RegisterTests` |
| Re-uploading a different ID asks before replacing edits | `jstests/autofill.test.js` |
| Valid login with a matching face; rejection on mismatch | `tests_face_login.FaceScanTests` |
| Liveness rejects a static image | `tests_face_login.FaceScanTests`, `tests_idscan.LivenessTests` |
| No face or several faces in the selfie | `tests_face_login.FaceScanTests` |
| Retries, then the alternative path | `tests_face_login.FallbackTests` |
| No face on the ID; duplicate ID image or number | `tests_registration.UploadTests`, `RegisterTests` |
| File signature, size and type | `tests_registration.UploadTests`, `tests_idscan.FileReadingTests` |
| Generic login error; expired sign-in | `tests.LoginPasswordTests`, `tests_face_login.FaceScanTests` |
| Storage, cropping, levelling, field mapping, template encryption | `tests_idscan` |
