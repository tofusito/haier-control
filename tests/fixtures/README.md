# Recorded hOn cloud responses

Real responses from the private hOn cloud, captured once from a live account and
redacted: MAC addresses replaced with `AA:BB:CC:DD:EE:*`, nicknames replaced,
and every credential, account id and geographic coordinate removed.

They exist because the field names and nesting of this API are not documented and
cannot be guessed. Four separate bugs shipped from guessing them
(`applianceTypeName` vs `applianceType`, the `setParameters.parameters` nesting,
and `tempIndoor` living in the context shadow). `tests/test_contract.py` parses
these files with the real driver code, so the next time hOn moves a field the
test fails instead of the dashboard silently going empty.

Re-capture after a confirmed cloud change only, and re-run the redaction audit
before committing: no `eyJ...` tokens, no `lat`/`lng`, no real MACs or emails.
