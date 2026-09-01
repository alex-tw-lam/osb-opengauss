# openGauss technical notes: logical databases, quotas and tablespaces

Reference for the openGauss features this broker builds on, based on the
official openGauss **7.0.0-RC3** SQL reference and administration guides,
cross-checked against the Huawei GaussDB enterprise documentation. The broker
only relies on community-edition openGauss SQL — no License-gated feature.

## 1. What "logical database" means in openGauss

* Community openGauss has **no `CREATE LOGICAL DATABASE` / PDB syntax**. The
  isolation unit is the **database**: within one instance, databases share very
  little and achieve *connection isolation* + *permission isolation*; they
  cannot cross-access data (except via FDW). This is what the docs call the
  database logical architecture, and it is the practical "logical database =
  tenant" building block.
* The Oracle-style **multi-tenant PDB** feature (one instance split into
  pluggable logical instances, managed on TPOPS) exists only in **GaussDB
  enterprise**, requires engine ≥ V2.0-8.200, host ≥ 16U128G and a **License**.
  Out of scope for a community broker.

So this broker implements a tenant as:

```
tenant = database + owner/readwrite/readonly group roles
       + REVOKE CONNECT FROM PUBLIC + ENABLE PRIVATE OBJECT
       + role-level space quotas + database CONNECTION LIMIT
```

## 2. `CREATE DATABASE` — every configurable parameter

Syntax (7.0.0-RC3):

```
CREATE DATABASE [IF NOT EXISTS] name
    [ [WITH] {
          OWNER [=] user_name
        | TEMPLATE [=] template
        | ENCODING [=] encoding
        | LC_COLLATE [=] lc_collate
        | LC_CTYPE   [=] lc_ctype
        | DBCOMPATIBILITY [=] compatibilty_type
        | TABLESPACE [=] tablespace_name
        | CONNECTION LIMIT [=] connlimit
    } [...] ];
```

| Parameter | Meaning | Value range / default | Broker usage |
|---|---|---|---|
| `name` | Database name | ≤63 chars identifier | `gdb_<instance_id>` |
| `OWNER` | Owning user | existing user; default = creator | tenant `_own` group role |
| `TEMPLATE` | Template DB | **only `template0`** in openGauss | always `template0` |
| `ENCODING` | Character set | UTF8, GBK, GB18030, Latin1, BIG5, EUC_*, ISO_8859_5-8, JOHAB, KOI8R/U, LATIN1-10, MULE_INTERNAL, SJIS, SHIFT_JIS_2004, SQL_ASCII, UHC, WIN866/874/1250-1258 | instance parameter `encoding` |
| `LC_COLLATE` | String sort order | valid locale, default from template; `C`/`POSIX` allow any encoding | kept `C` |
| `LC_CTYPE` | Character classification | valid locale, default from template | kept `C` |
| `DBCOMPATIBILITY` | SQL dialect: `A`=Oracle, `B`=MySQL, `C`=Teradata, `PG`=PostgreSQL (some builds also accept `O`/`OG` alias and use it as default — always pass it explicitly) | instance parameter `compatibility` |
| `TABLESPACE` | Default tablespace | existing tablespace, default `pg_default` | instance parameter `tablespace` |
| `CONNECTION LIMIT` | Max concurrent connections | int ≥ -1, default -1; **not enforced for sysadmin**; in distributed setups counted per CN (total = limit × CNs) | from plan / `max_connections` |

Constraints: cannot run inside a transaction block; caller needs `CREATEDB` or
sysadmin; if `ENCODING`/locale differ from the template you must use
`template0` (which openGauss requires anyway).

## 3. `ALTER DATABASE` — post-create configurables

| Clause | Effect |
|---|---|
| `CONNECTION LIMIT [=] n` | resize the connection cap (docs suggest 1–50 for shared systems) |
| `RENAME TO new_name` | rename (cannot be connected to it) |
| `OWNER TO new_owner` | change owner (needs CREATEDB) |
| `SET TABLESPACE ts` | move default-tablespace objects (physically moves data!) |
| `SET/RESET configuration_parameter` | **per-database GUCs** — a very large configuration surface (`work_mem`, `timezone`, `enable_indexscan`, `default_transaction_isolation`, …), effective next session |
| **`ENABLE/DISABLE PRIVATE OBJECT`** | Object isolation: ordinary users only see objects they hold privileges on (tables, views, columns, functions); admins unaffected. Must be executed while connected **to that database** |

`PRIVATE OBJECT` + `REVOKE CONNECT ... FROM PUBLIC` are the two isolation
pillars the broker relies on.

## 4. Storage & session quotas — where "storage size" really lives

openGauss has **no per-database storage quota** in the community edition.
Quotas are per **user/role** (`CREATE/ALTER USER|ROLE`):

| Clause | Meaning | Format |
|---|---|---|
| `PERM SPACE 'limit'` | permanent storage the user's objects may occupy | size string, e.g. `'5G'` (K/M/G/T/P), or `'unlimited'` |
| `TEMP SPACE 'limit'` | temp-table space quota | same |
| `SPILL SPACE 'limit'` | operator spill-to-disk quota (sort/hash overflow) | same |
| `CONNECTION LIMIT n` | per-user concurrent connection cap | int, default -1 |
| `RESOURCE POOL 'pool'` | bind user to a resource pool (must exist in `pg_resource_pool`) | pool name |
| `VALID BEGIN/UNTIL 'ts'` | account validity window | timestamps |
| `ACCOUNT LOCK/UNLOCK` | lock account | — |

Enforcement of the space quotas is part of the resource-load-management stack:
on many deployments you must enable the workload manager
(`enable_resource_track`, `use_workload_manager`/cgroups) for quotas to bite.
The broker sets identical quotas on the tenant owner role **and** on every
binding user, so personal-schema objects cannot bypass the tenant quota.

## 5. Tablespaces — sizing at the storage layer

`CREATE TABLESPACE` (7.0.0-RC3):

```
CREATE TABLESPACE tablespace_name
    [ OWNER user_name ] [RELATIVE] LOCATION 'directory' [ MAXSIZE 'space_size' ]
    [ WITH ( filesystem | random_page_cost | seq_page_cost ) ];
```

| Parameter | Notes |
|---|---|
| `OWNER` | defaults to creator; sysadmin may assign to non-sysadmin users |
| `RELATIVE` | LOCATION becomes relative to each node's data dir (`/pg_location/`), max two path levels — **the directory is created for you**, no shell access needed |
| `LOCATION 'dir'` | absolute path (or relative with RELATIVE); must be local, no special chars, not inside the data dir; empty and writable by the openGauss OS user |
| `MAXSIZE 'size'` | **maximum tablespace size on a single database node**; units KB/MB/GB/TB/PB (parsed as KB); omit = unlimited. Resizable later via `ALTER TABLESPACE … RESIZE MAXSIZE { UNLIMITED \| 'size' }`; if the new quota is below current usage the change still succeeds but writes are blocked until usage drops under the limit |

Permissions: sysadmin or members of the built-in role `gs_roles_tablespace`.
Not allowed inside transaction blocks; failed creates can leave residual
directories. HCS discourages user tablespaces (default storage assumed).

### Two ways to size a logical database — comparison

| | `role_quota` mode (PERM SPACE) | `tablespace` mode (MAXSIZE) |
|---|---|---|
| Where enforced | workload manager counts bytes per role/user | storage layer caps the tablespace files per node |
| Needs workload management on | **yes** (`enable_resource_track` etc.) | no (storage-level) |
| Granularity | per role (owner + binding users) | per tenant (dedicated tablespace, one per instance) |
| Covers temp/spill | yes (TEMP/SPILL SPACE alongside) | no — temp/spill still need role quotas |
| Resizing | re-stamp `PERM SPACE` on the roles | `ALTER TABLESPACE … RESIZE MAXSIZE` (writes blocked if new quota < current usage until it drops) |
| Caveats | role changes won't retro-check existing usage | needs sysadmin; leftover dirs on failures |

The broker implements both (`GAUSSDB_STORAGE_MODE=role_quota|tablespace`);
in `tablespace` mode it creates `gdb_<id>_ts` with
`RELATIVE LOCATION 'broker/gdb_<id>_ts' MAXSIZE '<plan>GB'` and points the
database's default tablespace at it, while TEMP/SPILL remain role quotas.

## 6. Account structure (for bindings)

openGauss users and roles are cluster-wide; schemas are per-database. Relevant
attributes with their `CREATE USER` defaults (all default to the negative
variant):

`SYSADMIN, MONADMIN, OPRADMIN, POLADMIN, AUDITADMIN, CREATEDB, CREATEROLE,
USEFT (reserved), INHERIT, LOGIN (CREATE USER ⇒ default LOGIN; CREATE ROLE ⇒
NOLOGIN), REPLICATION, INDEPENDENT (admins locked out of its objects without
authorization), VCADMIN (no meaning), PERSISTENCE, NODE GROUP (logical
cluster), PERM/TEMP/SPILL SPACE, CONNECTION LIMIT, VALID BEGIN/UNTIL,
RESOURCE POOL, PROFILE (ignored this version), DEFAULT TABLESPACE (ignored),
SYSID (ignored), PGUSER (reserved).`

Separation-of-duties note: with three-power separation
(`enable_separation_of_duty`) enabled, system administration / audit
administration / security-policy administration
are distinct. Tenant binding users need **none** of the admin attributes —
plain `LOGIN` + group-role membership is the access boundary.

Password policy: ≥8 chars, ≥3 of 4 character classes, must not equal the
username or its reverse; special-char set `~!@##$%^&*()-_=+\|[{}];:,<.>/?`.
`INDEPENDENT` users are interesting for hostile-tenant scenarios (admins
cannot read their tables) but block broker housekeeping (`DROP OWNED`), so the
broker does not use them by default.

Default privilege machinery used by the broker:

```sql
GRANT CONNECT ON DATABASE db TO rw, ro;      REVOKE CONNECT ON DATABASE db FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA s TO rw;       GRANT USAGE ON SCHEMA s TO ro;
ALTER DEFAULT PRIVILEGES FOR ROLE own IN SCHEMA s GRANT SELECT ON TABLES TO ro;
ALTER DEFAULT PRIVILEGES FOR ROLE own IN SCHEMA s GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rw;
ALTER DEFAULT PRIVILEGES FOR ROLE own IN SCHEMA s GRANT USAGE, SELECT ON SEQUENCES TO ro, rw;
```

`CREATE USER` also auto-creates a same-named schema in the database where it
runs — the broker cleans those up on unbind.

## 7. Resource pools (optional, per-tenant CPU/memory/IO)

```
CREATE RESOURCE POOL pool [ WITH ( MEM_PERCENT = 1..100 (default 20 multi-tenant)
       | MEMORY_LIMIT = '1KB..2047GB'
       | CONTROL_GROUP = "class:workload[:level]" | "High|Medium|Low|Rush"
       | ACTIVE_STATEMENTS = -1..2147483647
       | MAX_DOP = 1..64
       | io_limits = 0..2147483647
       | io_priority = Low|Medium|High|None ) ];
```

Requires cgroups prepared and workload management on; only SYSADMIN/VCADMIN
create pools. A future plan dimension (cpu shares / memory / iops per tenant)
would be `CREATE RESOURCE POOL` + `ALTER USER ... RESOURCE POOL`.

## 8. Driver note (openGauss ↔ psycopg2)

openGauss is PostgreSQL-9.2.4 wire-compatible; psycopg2 works, but openGauss
defaults to sha256 password auth. Either configure the admin user for md5
(`password_encryption_type = 1` + matching `pg_hba.conf`) or use the
openGauss-specific Python driver instead of psycopg2.

## 9. Sources

* CREATE DATABASE — docs.opengauss.org/en/docs/7.0.0-RC3/sql_reference/create_database.html
* ALTER DATABASE — .../alter_database.html (PRIVATE OBJECT, SET/RESET GUC)
* CREATE USER / CREATE ROLE — .../create_user.html, .../create_role.html
* CREATE TABLESPACE — .../create_tablespace.html (RELATIVE LOCATION, MAXSIZE)
* CREATE RESOURCE POOL — .../create_resource_pool.html
* Default Permission Mechanism / Managing Users and Their Permissions — Database Administration Guide
* GaussDB multitenant management (PDB, License-gated) — support.huawei.com/enterprise/en/doc/EDOC1100488514/f99a5c8a
* GaussDB(DWS) SPILL SPACE example — bbs.huaweicloud.com/blogs/312609
