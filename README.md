# Library Service API

A REST API for a library: manage the book catalogue, register readers, authenticate
with JWT, issue books and record returns while tracking available copies.

## Technologies

Python 3.14, Django, Django REST Framework, SQLite, SimpleJWT, drf-spectacular
(OpenAPI 3 / Swagger UI), and coverage.py. Versions are pinned in requirements.txt.

## Install from scratch

The following commands use PowerShell on Windows:

```powershell
git clone https://github.com/lukianlukian/library-service.git
cd library-service
git switch develop
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

On Linux/macOS, use `python3.14 -m venv .venv` and `source .venv/bin/activate`.
Run all subsequent commands from the repository root with the environment active.

## Configuration

Settings read process environment variables. `.env` is a convenience template;
it is **not loaded automatically**. Edit it, then export its values to your shell.
For PowerShell:

```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
```

For Linux/macOS: `set -a; . ./.env; set +a`.

| Variable | Local default | Meaning |
| --- | --- | --- |
| DJANGO_SECRET_KEY | Development-only fallback | Set a long random value outside local development. Never commit real secrets. |
| DJANGO_DEBUG | true | Set false outside local development. |
| DJANGO_ALLOWED_HOSTS | localhost,127.0.0.1 | Comma-separated allowed hostnames. |

SQLite is stored at `db.sqlite3`; no external database service is required.
The `.env` and database are ignored by Git. The Django development server is for
local development; production hosting requires separate server/security setup.

## Initialize and run

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The administrator signs in using email. Django admin: http://127.0.0.1:8000/admin/.
Swagger UI: http://127.0.0.1:8000/api/docs/.
OpenAPI schema: http://127.0.0.1:8000/api/schema/.

## Authentication and Swagger walkthrough

1. Open Swagger and execute `POST /api/users/` with email, a strong password,
   and optional first_name/last_name. Passwords are validated by Django, hashed,
   never returned, and cannot grant staff/superuser privileges.
2. Execute `POST /api/users/token/` with email and password:

   ```json
   {"email": "reader@example.com", "password": "your-strong-password"}
   ```

3. Copy `access`, click **Authorize**, and paste the token alone. Swagger adds
   `Authorization: Bearer <access>` automatically. For other clients, send this
   header yourself. Use `POST /api/users/token/refresh/` with `{"refresh":"..."}`
   to obtain a new access token; a refresh token cannot authorize API requests.
4. Use an administrator token to create a book through `POST /api/books/`:

   ```json
   {"title":"Dune","author":"Frank Herbert","inventory":2,"daily_fee":"1.50","cover":"HARD"}
   ```

5. Authorize with a reader token and execute `POST /api/borrowings/` with the
   returned book ID and an expected return date of today or later:

   ```json
   {"book":1,"expected_return_date":"2030-12-31"}
   ```

   Supply a new UUID in the required `Idempotency-Key` header (PowerShell:
   `[guid]::NewGuid()`). Keep that key and the exact request body for retries.
   The response includes a read-only payments array with the Checkout URL.

6. Execute `POST /api/borrowings/{id}/return/` using the borrowing ID, with no body.
   The server records today's date and restores one copy. A second return is 400
   and leaves inventory unchanged. The owner or staff may return the book.

## Endpoint reference

| Endpoint | Methods | Access / behavior |
| --- | --- | --- |
| /api/books/ | GET, POST | Public catalogue; staff only for creation. |
| /api/books/{id}/ | GET, PUT, PATCH, DELETE | Public reads; staff only for writes. |
| /api/users/ | POST | Public registration. |
| /api/users/token/ | POST | Public email/password exchange for access and refresh tokens. |
| /api/users/token/refresh/ | POST | Public refresh-token exchange for an access token. |
| /api/users/me/ | GET, PUT, PATCH | JWT required; always uses request.user. Password changes are validated and hashed with set_password. |
| /api/borrowings/ | GET, POST | JWT required. Readers see their own records; staff see all. |
| /api/borrowings/{id}/ | GET | Owner or staff; other readers receive 404. |
| /api/borrowings/{id}/return/ | POST | Owner or staff; atomic return and inventory increment. |

Books require non-negative inventory and daily_fee; cover is HARD or SOFT.
Books referenced by borrowings are protected from deletion. Borrowing creation
accepts only book and expected_return_date. The id, user, borrow_date and
actual_return_date fields are read-only; supplied values are ignored. PUT, PATCH
and DELETE are unavailable for borrowings (405).

Borrowing list filters:

- `?is_active=true`: actual_return_date is null; `false`: already returned.
- `?user_id=2`: staff only. For readers this parameter is ignored and their own
  records are returned. Combine both filters as needed.
- Invalid is_active or an invalid staff user_id returns 400.

Missing/invalid JWT returns 401. Authenticated non-staff book writes return 403.
Missing or inaccessible borrowing IDs return 404. Invalid dates, unavailable books
and repeated returns return 400 without partial database changes. Creation and
return use conditional SQL updates with F expressions in a transaction, so the
inventory checks also work with SQLite (no reliance on select_for_update).

## Tests, coverage and schema validation

```powershell
python manage.py test
coverage run --source=books,users,borrowings,payments manage.py test
coverage report -m --fail-under=60
python manage.py spectacular --file schema.yml --validate
python manage.py check
git diff --check
```

`.coveragerc` excludes virtual environments, migrations and test code. The explicit
`--source` command above measures the three API apps; plain `coverage run manage.py
test` also includes config as configured in `.coveragerc`. The minimum coverage is
60%. Optional HTML output: `coverage html`, then open `htmlcov/index.html`.
Coverage reports and the generated schema.yml are ignored by Git; regenerate them
locally. Tests cover authentication, permissions, validation, database changes,
rollback and concurrent borrowing/return operations.

## Stripe test payments

Set `STRIPE_SECRET_KEY` to your Stripe **sk_test_** secret and
`STRIPE_PUBLIC_BASE_URL` to the base URL serving this API (default
`http://127.0.0.1:8000`). Export them using the configuration steps above.
Only test keys and test Checkout sessions are accepted. Never put keys in source
code, screenshots, commits or request bodies. `.env` remains ignored.

Rental days = `max(1, (expected_return_date - borrow_date).days)`: the return day
is excluded; a same-day rental costs one day. USD total = daily_fee × days using
Decimal, rounded to cents; for example 1.25 × 3 = 3.75 USD (375 cents sent to
Stripe). Zero-price rentals are rejected. The USD card minimum is 0.50; totals
above 999999.99 are rejected before reserving inventory. Stored totals/currency
are immutable snapshots even if the book price later changes. FINE exists only
as a model type; fine calculation is not implemented.

Creating a borrowing reserves one copy and creates its PENDING Payment atomically.
After that transaction commits, Stripe Checkout is called without a book lock.
Open the returned payments[0].session_url and use Stripe's test card
`4242 4242 4242 4242`, any future expiry and any three-digit CVC. Do not use a real
card. The redirect `GET /api/payments/success/?session_id=...` fetches the session
from Stripe and verifies test mode, complete/paid status, identity, stored amount
and currency before marking PAID. A browser redirect alone is not proof of payment.
Repeated success is harmless. `GET /api/payments/cancel/?session_id=...` changes
nothing and returns the same URL so a PENDING payment can be resumed.
These redirect endpoints are public because redirects do not carry a Bearer
header; the unguessable session ID is a capability. They expose no user details.

On Stripe/network failure the API returns 503 with borrowing_id/payment_id and
the original key. Retry the same POST body and Idempotency-Key. It reuses the
reservation, price, saved Stripe request parameters and Stripe idempotency key;
inventory is not decremented again. A successful replay returns 200 instead of
201. A key reused for another body/user returns 409. Never use a new key to retry
an ambiguous network failure. Unknown outcomes older than 23 hours return 409
for manual Stripe reconciliation: Stripe can discard idempotency keys after 24h,
so blindly recreating such a session could allow a duplicate payment. No new
session is automatically created for expired checkouts; use the authorized renewal
endpoint described below.

Automated payment tests mock Stripe and never require real credentials. For a
manual test, obtain JWT, issue a book, open Checkout in test mode, complete the
test payment, then verify the success response says PAID and a repeated callback
leaves inventory unchanged. Cancel another test checkout and confirm PENDING.

Stripe references: [Checkout Sessions](https://docs.stripe.com/api/checkout/sessions/create),
[idempotent requests](https://docs.stripe.com/api/idempotent_requests).
## Continuous integration

GitHub Actions checks every PR into develop/main and every push to these branches:
flake8, Black, missing migrations, Django checks, OpenAPI validation, tests and
coverage (minimum 60%). No Stripe or Telegram credentials are supplied to CI;
external-service tests use mocks. Run `python -m flake8 .` and
`python -m black --check --workers 1 .` locally before pushing.

## Background Telegram notifications

New borrowings are rejected with HTTP 400 and a `payment` error while the reader
has any PENDING payment (rental or fine), without changing inventory. The check
and reservation are serialized per user, including simultaneous requests. A replay
of the original Idempotency-Key is still allowed so a failed checkout can recover.
Another user's debt does not restrict this user; PAID payments do not block.

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS (comma-separated administrator chat
IDs). The bot must already be allowed to message these chats. Leave both blank to
disable notifications. Never commit the bot token or personal chat IDs.

Start Redis and run migrations, then use separate terminals with the same exported
environment variables:

```powershell
docker compose up -d redis
python manage.py migrate
python manage.py runserver
# Terminal 2 (Windows local development):
celery -A config worker --pool=solo --loglevel=info
# Terminal 3 (run exactly one scheduler):
celery -A config beat --loglevel=info
```

On Linux/WSL use `celery -A config worker --loglevel=info`. Windows solo is for
local testing; Celery does not officially support Windows. Redis is bound to
127.0.0.1:6379; CELERY_BROKER_URL defaults to redis://127.0.0.1:6379/0.
Notification tasks are published after commit. An outbox row is stored in the
same transaction as the borrowing or PAID transition. Queue failures do not fail
the API request; a minute dispatcher republishes unsent events. Each event/chat
has at most four delivery attempts, with task retry backoff 5/10/20 seconds.
Failed rows remain inspectable; attempts are never reset automatically.

Administrators receive new-borrowing and confirmed-payment messages. The scheduler
checks overdue borrowings every day at 09:00 UTC: actual_return_date is null and
expected_return_date is before today. It sends book, borrowing/user IDs and due
date, once per borrowing/chat/day. Notification deduplication uses event keys and
a delivery lease. Telegram has no idempotent send API: a process crash after a
successful send but before saving sent_at can still cause a duplicate. Payment
and inventory operations remain idempotent independently of notifications.

Run notifications tests with `python manage.py test notifications`. Include this
app in explicit coverage commands:
`coverage run --source=books,users,borrowings,payments,notifications manage.py test`.

## Expired Checkout sessions and renewal

Celery Beat runs payments.tasks.check_payment_sessions every minute. The worker
retrieves each PENDING current session from Stripe outside a database transaction.
Verified expired/unpaid sessions become EXPIRED; verified complete/paid sessions
are reconciled to PAID even when the customer missed the success redirect. Stripe
errors or mismatched amount/currency/identity leave the record unchanged for the
next scheduled check. Missing test credentials disable this check.

POST /api/payments/{id}/renew/ requires JWT for the owner or staff. Send no body
and a **new UUID** Idempotency-Key. Only EXPIRED payments can start a new generation;
ordinary PENDING or PAID payments return 409. The same renewal key can safely retry
an in-progress PENDING renewal after a network failure. It returns the new session
URL and changes status to PENDING. Each attempt has a saved key/timestamp and
historical session record. Totals, currency, borrowing and inventory do not change.
A different retry key is rejected while renewal is in progress. Unknown outcomes
older than 23 hours require manual reconciliation; do not generate another key.
Old session callbacks return 404 after renewal and cannot pay/expire the new session.
The debt restriction is specifically for PENDING payments, not EXPIRED records.
