# Haier hOn protocol notes

Research date: 2026-09-04. Source snapshot:
[`tis24dev/addhOn` commit `7442433`](https://github.com/tis24dev/addhOn/commit/7442433af46411ed94db64fdfb234574788ee7b9).

The primary interoperability reference is addhOn's public, independently authored
[Haier hOn transport specification](https://github.com/tis24dev/addhOn/blob/main/docs/protocol/HAIER-HON-TRANSPORT.md).
The upstream project is
[AGPL-3.0](https://github.com/tis24dev/addhOn/blob/main/LICENSE). Haier Control uses the
wire facts below but contains no copied addhOn code.

## Verified contract

- Authentication is an OAuth/OpenID implicit flow fronted by Salesforce at
  `account2.hon-smarthome.com`: authorize, Salesforce Aura login form, token redirect,
  then `POST /auth/v1/login` on the IoT API to obtain a Cognito token.
- Email 2FA appears as a Salesforce `ProgressiveLogin` page. The code is sent and verified
  with JS Remoting (`resendEmailCode`, `verifyEmailOTP`), followed by a Visualforce
  postback and a new OAuth authorize request on the same cookie-bound session.
- addhOn's [config flow](https://github.com/tis24dev/addhOn/blob/main/custom_components/addhon/config_flow.py)
  presents email/password, pauses while the live backend session owns the OTP challenge,
  then resumes with the code. Haier Control follows the same state boundary without
  depending on Home Assistant.
- Authenticated IoT calls go to `https://api-iot.he.services` and carry both exact headers
  `cognito-token` and `id-token`.
- Appliance discovery is `POST /unified-api/v1/view/appliance-list`; appliances are read
  from `modules.applianceList.payload.appliances`. Unexpected shapes are errors, not empty
  success.
- Command schema is read from `GET /commands/v1/retrieve`; state comes from
  `GET /commands/v1/context`. Statistics, history, maintenance and other reads exist but
  are not necessary for the v0.1 primary controls.
- Commands use `POST /commands/v1/send` with a strict envelope. Only
  `payload.resultCode == "0"` is accepted. UTC timestamps have exactly millisecond
  precision and a `Z` suffix.
- AWS IoT MQTT over WebSocket uses a custom authorizer and is valuable for latency, but
  polling/reconciliation remains required. MQTT is deferred until real credentials can
  validate the full lifecycle safely.
- 401/403 triggers refresh/reauth, 429 is rate limiting, 5xx is transient, and non-JSON
  bodies are failures. Scraped login URLs are pinned to the known Salesforce host to
  prevent SSRF.

## AC normalization

The live command schema is authoritative. Known translations are applied only to values
the model advertises: `machMode` maps auto/cool/dry/heat/fan, `tempSel` supplies its own
range/step, `windSpeed` supplies fan values, and each swing axis supplies its own allowed
positions. Advanced switches appear only when their raw setting exists in the schema.

Two AC write families are publicly documented: some write power/mode through `settings`,
others use `startProgram`/`stopProgram`. v0.1 safely supports the settings-based path and
rejects an unadvertised control. Program-based units remain a known extension; the app
must not guess a program envelope.
