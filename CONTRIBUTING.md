# Contributing

Thanks for helping improve Haier Control. Keep pull requests small and focused.

Before opening a change, search existing issues. Never include hOn credentials, tokens,
JWTs, MAC addresses, coordinates or raw cloud responses containing personal data.

Use English for interface text, documentation, comments and synthetic examples. Device
names received from hOn are user content and are displayed as supplied.

Python 3.12+ is required; CI currently runs Python 3.13. Node.js is needed for the UI
state tests. Use `python -m scripts.demo` for a local mock demonstration.

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
