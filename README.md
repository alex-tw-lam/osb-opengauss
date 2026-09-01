# osb-opengauss

An [Open Service Broker API](https://www.openservicebrokerapi.org/) broker in
Python that turns a shared **openGauss / GaussDB** instance into a
self-service catalog:

| OSB concept | openGauss implementation |
|---|---|
| Service offering `gaussdb` | one openGauss instance (admin connection via env vars) |
| Service plan | quota bundle: `PERM/TEMP/SPILL SPACE` + database `CONNECTION LIMIT` |
| Service instance (tenant) | a **logical database** + `own`/`rw`/`ro` group roles, `REVOKE CONNECT FROM PUBLIC`, `ALTER DATABASE ... ENABLE PRIVATE OBJECT` |
| Binding | a **login user account** (`CREATE USER`), member of one group role (access boundary), with its own `CONNECTION LIMIT` and the plan's space quotas |

Built with [uv](https://docs.astral.sh/uv/),
[openbrokerapi](https://pypi.org/project/openbrokerapi/) (a Python
implementation of the Open Service Broker API, exposed as a Flask blueprint)
and psycopg2.

The full parameter reference for openGauss logical databases, quotas and
tablespaces is in [`docs/opengauss-research.md`](docs/opengauss-research.md).

## Code layout — one responsibility per file

Each module owns one job, stated in its first docstring lines. Dependencies
point one way only (app → broker → plans/params/gaussdb/state → config).
Code is identical in every deployment; environment-specific values live in
exactly two places: `plans.toml` (the offering) and environment variables
(infrastructure wiring).

| File | Responsibility |
|---|---|
| `app.py` | Flask wiring: builds the app, registers the OSB blueprint |
| `broker.py` | OSB layer: maps API calls to admin + state calls; no SQL |
| `plans.py` | Loads `plans.toml` (data), validates it, assembles the OSB catalog |
| `params.py` | Request rules: parameter validation and the matching JSON schemas |
| `gaussdb.py` | All openGauss DDL; knows SQL, not the OSB API |
| `state.py` | SQLite tables recording what the broker created |
| `config.py` | Environment variable names and their parsing |
| `plans.toml` | **Data**: the plan catalog (quota bundles) offered by this deployment |

## Plans

Plans live in [`plans.toml`](plans.toml) — deployment **data**, not code.
Each environment (lab / staging / prod) carries its own copy; the broker
loads and validates it at startup and refuses to start on a missing,
malformed or duplicate-id file. Fields:

| Field | Meaning | Maps to |
|---|---|---|
| `id` / `name` / `description` | plan identity — never rename an `id` once instances exist on it | catalog |
| `storage_gb` | storage quota | `PERM SPACE` / tablespace `MAXSIZE` |
| `temp_gb` / `spill_gb` | temp / operator-spill quotas | `TEMP SPACE` / `SPILL SPACE` |
| `max_connections` | max concurrent connections | database `CONNECTION LIMIT` |
| `free` | optional, defaults to `true` | catalog billing hint |

Optional provision parameters (JSON-schema validated, may only tighten the
plan): `compatibility` (`PG`/`A`/`B`/`C`), `encoding`
(`UTF8`/`GBK`/`GB18030`/`Latin1`), `tablespace` (enum of operator-curated
tablespaces, see below), `max_connections`, `storage_gb`, `temp_gb`,
`spill_gb`. Bind parameters: `access_role` (`owner`/`readwrite`/`readonly`,
default `readwrite`) and `max_connections`. Everything user-selectable is an
enum or a bounded integer — no free-form identifiers.

## How storage sizing is enforced

openGauss has no native per-database quota, so the broker supports two modes
(`GAUSSDB_STORAGE_MODE`):

* **`role_quota` (default)** — the plan's storage number becomes
  `PERM SPACE` on the tenant's owner role and every binding user (plus
  `TEMP SPACE` / `SPILL SPACE`). Simple, but the workload manager must be
  enabled on the server for the quotas to be enforced.
* **`tablespace`** — every instance gets a dedicated tablespace
  (`gdb_<id>_ts`, created with `RELATIVE LOCATION 'broker/…'` so no shell
  access is needed) with `MAXSIZE '<plan>GB`, set as the database default
  tablespace. This is a hard storage cap at the storage layer (per node) and
  does not depend on workload management; the admin user must be sysadmin and
  temp/spill are still role quotas. Updating `storage_gb` maps to
  `ALTER TABLESPACE … RESIZE MAXSIZE` (if the new quota is below current
  usage, writes are blocked until usage drops under the limit).

In `role_quota` mode operators can expose pre-created tablespaces to users as
an enum via `GAUSSDB_TABLESPACES=ts_ssd,ts_hdd`; the `tablespace` provision
parameter only accepts values from that list.

## Quick start

```bash
uv sync

export GAUSSDB_HOST=... GAUSSDB_PORT=5432
export GAUSSDB_ADMIN_USER=... GAUSSDB_ADMIN_PASSWORD=...
export BROKER_USERNAME=broker BROKER_PASSWORD=$(openssl rand -hex 16)

uv run osb-opengauss            # dev server on 127.0.0.1:5000
```

Production:

```bash
uv run --with gunicorn gunicorn -w 2 -b 0.0.0.0:5000 'osb_opengauss.app:create_app()'
```

## Trying it with curl

```bash
AUTH='-u broker:broker-dev-password'
H='X-Broker-API-Version: 2.16'

# catalog
curl $AUTH -H "$H" localhost:5000/v2/catalog

# provision a dev tenant
curl $AUTH -H "$H" -X PUT localhost:5000/v2/service_instances/11111111-1111-1111-1111-111111111111 \
  -H 'Content-Type: application/json' \
  -d '{"service_id":"4c6f6a1e-0f5a-4a5b-9d7e-2f8b3a1c5e01","plan_id":"gaussdb-dev",
       "parameters":{"compatibility":"A","max_connections":10}}'

# bind a readonly app account
curl $AUTH -H "$H" -X PUT localhost:5000/v2/service_instances/11111111-1111-1111-1111-111111111111/service_bindings/22222222-2222-2222-2222-222222222222 \
  -H 'Content-Type: application/json' \
  -d '{"service_id":"4c6f6a1e-0f5a-4a5b-9d7e-2f8b3a1c5e01","plan_id":"gaussdb-dev",
       "parameters":{"access_role":"readonly"}}'
# → credentials: uri / hostname / port / database / username / password / jdbcUrl

# unbind, then deprovision
curl $AUTH -H "$H" -X DELETE "localhost:5000/v2/service_instances/11111111-1111-1111-1111-111111111111/service_bindings/22222222-2222-2222-2222-222222222222?service_id=4c6f6a1e-0f5a-4a5b-9d7e-2f8b3a1c5e01&plan_id=gaussdb-dev"
curl $AUTH -H "$H" -X DELETE "localhost:5000/v2/service_instances/11111111-1111-1111-1111-111111111111?service_id=4c6f6a1e-0f5a-4a5b-9d7e-2f8b3a1c5e01&plan_id=gaussdb-dev"
```

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `GAUSSDB_HOST` / `GAUSSDB_PORT` | `localhost` / `5432` | openGauss admin endpoint |
| `GAUSSDB_ADMIN_USER` / `GAUSSDB_ADMIN_PASSWORD` | `gaussdb` / — | must be sysadmin or `CREATEDB`+`CREATEROLE` |
| `GAUSSDB_ADMIN_DB` | `postgres` | database the broker connects to for DDL |
| `GAUSSDB_SSLMODE` | `disable` | libpq sslmode, propagated in binding URIs |
| `GAUSSDB_CONNECT_TIMEOUT` | `10` | admin connection timeout (seconds) |
| `BROKER_USERNAME` / `BROKER_PASSWORD` | `broker` / dev default | OSB basic auth |
| `STATE_DB_PATH` | `./osb-opengauss-state.sqlite3` | idempotency/credential store |
| `GAUSSDB_NAME_PREFIX` | `gdb` | prefix for created databases/roles/users |
| `GAUSSDB_STORAGE_MODE` | `role_quota` | `role_quota` or `tablespace` (see above) |
| `GAUSSDB_TABLESPACES` | *(empty)* | curated tablespace enum for the `tablespace` parameter |
| `GAUSSDB_PLANS_FILE` | `plans.toml` | the plan catalog data file (see Plans) |
| `GAUSSDB_TABLESPACE_LOCATION_PREFIX` | `broker` | single path segment under `pg_location/` (tablespace mode) |
| `DISABLE_SPACE_ORG_GUID_CHECK` | `true` | set `false` on Cloud Foundry (it sends org/space GUIDs; Kubernetes does not) |
| `BROKER_HOST` / `BROKER_PORT` | `127.0.0.1` / `5000` | dev server bind |

## Behaviour notes

* All operations are synchronous (`accepts_incomplete` unsupported).
* Idempotency per the OSB spec: identical repeated PUTs return the same
  credentials; conflicting PUTs → 409; unknown DELETEs → 410. Deprovisioning
  an instance that still has bindings → 400. Invalid parameters → 400 before
  any DDL runs.
* If the broker loses its state database but the openGauss objects still
  exist, the broker probes `pg_database`/`pg_roles` and answers 409 instead
  of failing on raw DDL — see `docs/cpm-tests.md` for the full
  category-partition analysis of these error paths.
* Object names derive from instance/binding IDs (`gdb_…`, `gdbu_…`), always
  identifier-quoted; passwords are random 28-char values meeting the openGauss
  complexity policy.
* openGauss defaults to sha256 auth; if psycopg2 cannot log in, set
  `password_encryption_type = 1` (md5) for the admin user or swap in the
  openGauss driver (`GaussDBAdmin._psycopg_connect` is one function).

## Tests

```bash
uv run pytest
```

Unit tests run against a fake connection that records SQL — no database
required. `tests/test_cpm.py` implements the category-partition test frames
documented in `docs/cpm-tests.md` (new/existing databases and users, valid
and invalid configurations, collisions, storage modes).

## Auditing the code

The full scan suite, in the form every commit should pass (all commands run
from the repository root; zero findings expected):

```bash
uv run pytest                                              # functional tests
uvx ruff check src tests                                   # lint / static analysis
uvx ruff format --check src tests                          # formatting
uvx mypy                                                   # type checking
uvx bandit -r src                                          # security SAST
uvx semgrep scan --config auto src                         # extended SAST
uvx vulture src tests vulture_whitelist.py                 # dead code
uvx codespell src tests docs README.md                     # typos
gitleaks detect --source .                                 # secrets in files and git history
uv run --with pytest-cov pytest --cov=osb_opengauss --cov-report=term-missing   # coverage
uv export --frozen --no-hashes --no-emit-project -o /tmp/req.txt \
  && uvx pip-audit -r /tmp/req.txt                         # dependency CVEs
```

`vulture_whitelist.py` records the names that implement the openbrokerapi
interface contract; `# nosec` / `# nosemgrep` markers in `gaussdb.py` mark the
deliberately hand-quoted SQL probes, each with a justification comment.
