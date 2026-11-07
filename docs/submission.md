# Submission evidence

## Pull requests

| Block | PR | Validation |
| --- | --- | --- |
| Users API | [#3](https://github.com/lukianlukian/library-service/pull/3) | API tests; merged |
| Borrowings API | [#4](https://github.com/lukianlukian/library-service/pull/4) | API, rollback and SQLite concurrency tests; merged |
| OpenAPI / README / coverage | [#5](https://github.com/lukianlukian/library-service/pull/5) | Schema validation and Swagger JWT/borrowing/return walkthrough; merged |
| Stripe payments | [#6](https://github.com/lukianlukian/library-service/pull/6) | Green GitHub Actions; Stripe mocks; merged |
| GitHub Actions | [#7](https://github.com/lukianlukian/library-service/pull/7) | Green GitHub Actions; merged before other feature PRs |
| Telegram / overdue | [#8](https://github.com/lukianlukian/library-service/pull/8) | Green GitHub Actions; Telegram mocks; Redis PONG and worker ping; merged |
| Pending debt restriction | [#9](https://github.com/lukianlukian/library-service/pull/9) | Green GitHub Actions; API/database checks; merged |
| Session expiration / renewal | [#10](https://github.com/lukianlukian/library-service/pull/10) | Green GitHub Actions; concurrent renewal and expiry tests; merged |

## Verified automatically

- Registration, JWT, book creation, borrowing, verified mock payment, notification
  delivery, return, session expiration, payment renewal and overdue notification
  are exercised together in payments.test_workflow.
- All API apps and notifications are included in coverage; tests and migrations
  are excluded. Required minimum: 60%.
- OpenAPI validates; JWT and read-only/write-only fields have contract checks.
- No forgotten model migrations; Django checks and diff checks pass.
- GitHub Actions runs lint, Black, migrations, schema, tests and coverage without
  real service credentials.
- Local Redis answered PONG; the worker registered all four tasks. Beat dispatched
  the two minute jobs, and worker statistics confirmed processing both jobs and
  an explicitly triggered overdue check. Superuser creation also succeeded in
  the clean temporary installation.
- A clean temporary Python environment installs requirements.txt successfully.
- In the clean copy, all migrations applied and all 80 tests passed; coverage for
  books/users/borrowings/payments/notifications was 96%. Schema validation, missing
  migration checks, Django checks and flake8 passed there as well.
- Tracked-file secret scanning found no Stripe, Telegram or GitHub token patterns;
  .env is untracked. This scan is a targeted check, not a claim to detect all
  possible secret formats.

## External acceptance still required

These items are **not** marked completed by mock tests:

- Complete one Checkout payment in your own Stripe test account with a test key
  and test card; verify PAID, cancellation, expiry and renewal against Stripe.
- Send an actual message using your Telegram bot to the intended administrator
  chats. Credentials/chat IDs are not available in the connected environment.
- Create/update the selected task cards in Trello and attach PR links. The Trello
  connection is not configured; no remote cards have been changed.
- Check selected tasks against the original assignment rubric. The full rubric
  was not supplied in this workspace; FINE is a model option only, with no fine
  calculation in this scope.

## Trello-ready task descriptions

Use one card per row in the PR table for the selected assignment tasks. Card title
is the Block name; attach its PR URL. Checklist: implementation, API/database tests,
OpenAPI/README, green CI, review and merge. Add the relevant Stripe/Telegram manual
acceptance item above and keep that item unchecked until it is actually verified.
Move a card to Done only when its selected acceptance criteria are satisfied.
