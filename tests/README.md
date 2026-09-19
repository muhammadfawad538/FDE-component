# M5 Test Suite

SP-05 conformance tests for `fde_component`.

## Test files

| File | Scope | Requires Frappe DB |
|------|-------|--------------------|
| `test_conformance.py` | SP-05 requirement contract tests | No |
| `test_dedup.py` | Idempotency key determinism | No |
| `test_dlq.py` | DLQ retry and dead-lettering | Yes |
| `test_idempotency.py` | Dedup DB operations | Yes |
| `test_checkpoint_resume.py` | Worker kill and resume | Yes |
| `test_api.py` | API endpoint contracts | No |

## Running tests

```bash
# Run all tests (requires bench environment)
bench --site <site> run-tests --app fde_component

# Run a single file
bench --site <site> run-tests --module fde_component.tests.test_conformance
```

## Test categories

- **Unit tests** (`test_conformance.py`, `test_dedup.py`, `test_api.py`): no DB required
- **Integration tests** (`test_dlq.py`, `test_idempotency.py`, `test_checkpoint_resume.py`): require live Frappe site
