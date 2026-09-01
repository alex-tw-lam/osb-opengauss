# Error handling — Category Partition Method analysis

The broker's request handling is tested with the Category Partition Method:
identify the aspects (parameters/conditions that change behaviour), partition
each aspect's possible values, combine partitions into test frames, then
instantiate each frame as a test case with a pre-declared expected result.
Implementation: `tests/test_cpm.py` (declarative `FRAMES` table + runner).

## Aspects and partitions

| # | Aspect | Partitions |
|---|---|---|
| A1 | operation | `provision` · `update` · `bind` · `unbind` · `deprovision` |
| A2 | instance record (broker state) | `absent` · `present` |
| A3 | binding record (broker state) | `absent` · `present` |
| A4 | request parameters | `default` · `valid-custom` · `conflicting` (≠ stored) · `invalid-enum` · `out-of-range` · `unknown-plan` · `ts-choice` · `bad-access-role` · `plan-change` |
| A5 | physical divergence (object exists in openGauss, broker state doesn't know) | `clean` · `db-name-collision` · `owner-role-collision` · `user-name-collision` |
| A6 | storage mode | `role_quota` · `tablespace` |
| A7 | bindings present at deprovision | `none` · `some` |

## Test frames → test cases (with expected results)

| Frame | Aspects (A1…A7) | Expected result |
|---|---|---|
| F01 | provision, absent, default, clean, role_quota | 201 `SUCCESSFUL_CREATED`; SQL: `CREATE DATABASE`, `PERM SPACE '5G'`, `ENABLE PRIVATE OBJECT` |
| F02 | provision, present, default | 200 `IDENTICAL_ALREADY_EXISTS`, no extra DDL |
| F03 | provision, present, conflicting | 409 `ErrInstanceAlreadyExists` |
| F04 | provision, absent, invalid-enum | 400 `ErrInvalidParameters`, no DDL |
| F05 | provision, absent, out-of-range | 400 `ErrInvalidParameters`, no DDL |
| F06 | provision, absent, unknown-plan | 400 `ErrInvalidParameters`, no DDL |
| F07 | provision, absent, db-name-collision | 409 `ErrInstanceAlreadyExists`, no `CREATE DATABASE` |
| F08 | provision, absent, owner-role-collision | 409 `ErrInstanceAlreadyExists`, no `CREATE DATABASE` |
| F09 | provision, absent, default, tablespace mode | created; `CREATE TABLESPACE … MAXSIZE '5GB'`, no `PERM SPACE` |
| F10 | provision, ts-choice, tablespace mode | 400 (dedicated tablespace already chosen) |
| F11 | provision, ts-choice, role_quota + curated enum | created with `TABLESPACE "ts_ssd"` |
| F12 | update, present, valid-custom | 200; `ALTER DATABASE … CONNECTION LIMIT = 10` |
| F13 | update, absent | 410 `ErrInstanceDoesNotExist` |
| F14 | update, present, plan-change | 400 `ErrPlanChangeNotSupported` |
| F15 | update, present, storage-resize, tablespace mode | 200; `ALTER TABLESPACE … RESIZE MAXSIZE '3GB'` |
| F16 | bind, instance present, binding absent, clean | 201 `Binding` with credentials; `CREATE USER`, `PERM SPACE '5G'` |
| F17 | bind, binding present, identical | 200 `IDENTICAL_ALREADY_EXISTS`, **same credentials** |
| F18 | bind, binding present, conflicting | 409 `ErrBindingAlreadyExists` |
| F19 | bind, instance absent | 410 `ErrInstanceDoesNotExist` |
| F20 | bind, binding absent, user-name-collision | 409 `ErrBindingAlreadyExists`, no `CREATE USER` |
| F21 | bind, binding absent, bad-access-role | 400 `ErrInvalidParameters`, no `CREATE USER` |
| F22 | unbind, binding present | 200; `DROP OWNED BY`, `DROP USER`; state row cleared |
| F23 | unbind, binding absent | 410 `ErrBindingDoesNotExist` |
| F24 | deprovision, present, bindings=some | 400 `ErrInvalidParameters`; instance untouched |
| F25 | deprovision, present, bindings=none (after unbind) | 200; `DROP DATABASE IF EXISTS`, `DROP ROLE IF EXISTS`; state cleared |
| F26 | deprovision, absent | 410 `ErrInstanceDoesNotExist` |
| F27 | deprovision, present, tablespace mode | 200; also `DROP TABLESPACE IF EXISTS` |

## Error → HTTP mapping (provided by openbrokerapi)

| Exception (raised by the broker) | HTTP |
|---|---|
| `ErrInvalidParameters` (incl. `ErrPlanChangeNotSupported`) | 400 |
| `ErrInstanceAlreadyExists` / `ErrBindingAlreadyExists` | 409 |
| `ErrInstanceDoesNotExist` / `ErrBindingDoesNotExist` | 410 |

Collision detection (A5) is implemented in `GaussDBAdmin._exists`, which
probes `pg_database` / `pg_roles` / `pg_tablespace` before creating anything;
the broker maps the resulting `AlreadyExistsError` to the appropriate 409 so
that a lost state database degrades into clean conflicts instead of raw DDL
failures.
