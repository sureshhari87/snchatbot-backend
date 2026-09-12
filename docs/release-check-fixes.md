# Release check fixes - 2026-09-12

Changes remain local on `codex/fastapi-commerce-migration`, based on
`bf259aa` (`feature/refresh-token-auth`). No production deploy or migration
was performed. The existing GitHub workflow deploys only from `main`;
the repository's default branch is currently `master`. Do not bypass the
staging gate or assume a feature-branch push deploys production.

## Corrections

- Applied the configured formatter and corrected imports/exception chaining.
- Restricted integration requests to HTTP(S), rejected embedded URL credentials,
  and rejected cross-origin redirects to protect integration authorization headers.
- Replaced python-jose with PyJWT[crypto] to remove the vulnerable ecdsa dependency
  from fresh installations. Firebase certificate verification converts X.509
  certificates to public keys and retains RS256, audience and issuer checks.
- Added real RSA signature regression coverage, certificate rotation/cache tests,
  invalid-token tests, HTTP transport/security tests and legacy pricing tests.
- Validated the operator-selected pg_dump executable and kept database credentials
  out of process arguments. The two narrowly scoped Bandit subprocess annotations
  document this operator-only, absolute-path, shell-free invocation.

## Local verification

Windows, Python 3.14.2:

- Full existing CI coverage command: 272 passed, 5 live tests skipped;
  80.96% coverage with the original 80% threshold unchanged.
- `ruff check .`: passed.
- `ruff format --check .`: passed.
- `bandit -c pyproject.toml -r . -q`: passed.
- `pip-audit --progress-spinner off -r requirements.txt`: no known vulnerabilities.
- `python scripts/secret_hygiene_check.py`: passed.

Python 3.11/3.12 and Docker are not installed on the validation machine.
Their GitHub matrix/build checks remain required after publishing the changes.
Use a fresh deployment environment from requirements.txt; an existing virtualenv
can retain unused packages from older requirements.

## Database identity is still unresolved

The supplied Neon backup was checksum-verified and successfully restored into
a password-protected, loopback-only local PostgreSQL 18.6 instance. The local
copy migrated from 0012_firebase_commerce_identity to 0013_commerce_cart;
the server was stopped afterward. Production was not modified.

The backup contained zero users and zero products. The public production API
still returned four demo catalogue products (IDs 1, 2, 3, 4). This does not
establish that the supplied backup is for the database used by Hugging Face.

In Neon SQL Editor, select the intended branch and database and run:

```sql
SELECT current_database() AS database_name,
       (SELECT count(*) FROM public.products) AS products,
       (SELECT count(*) FROM public.users) AS users;
```

Record only the branch/database names and counts. Matching counts alone do not
prove identity. Compare the selected Neon endpoint and database with the original
connection configured as Hugging Face DATABASE_URL, privately. Hosting APIs cannot
read back the secret value. Do not overwrite that secret merely to make it match
an empty backup. Back up the confirmed production database before migration.

Real inventory, delivery coverage and isolated Razorpay test-mode configuration
are still required before a production commerce cutover.
