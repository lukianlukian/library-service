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
coverage run --source=books,users,borrowings manage.py test
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

## Continuous integration

GitHub Actions checks every PR into develop/main and every push to these branches:
flake8, Black, missing migrations, Django checks, OpenAPI validation, tests and
coverage (minimum 60%). No Stripe or Telegram credentials are supplied to CI;
external-service tests use mocks. Run `python -m flake8 .` and
`python -m black --check --workers 1 .` locally before pushing.
