# Contributing

Thanks for helping improve Haier Control. Keep pull requests small and focused.

Before opening a change, search existing issues. Never include hOn credentials, tokens,
JWTs, MAC addresses, coordinates or raw cloud responses containing personal data.

Run the same checks as CI:

```sh
pip install -e '.[dev]'
ruff check .
mypy app tests
pytest -q
node --test tests/test_ui_state.js
bash -n scripts/deploy-homelab.sh
```

New behavior should have tests. Cloud-contract changes need a redacted fixture and an
explanation of the observed response shape. Do not run real appliance commands in CI.
