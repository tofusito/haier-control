# Compatibility

Haier Control talks to the private hOn cloud API, not directly to the air conditioner.
Compatibility therefore depends on both the appliance firmware and the cloud response
shape. The cloud driver must remain opt-in because the upstream API is undocumented and
can change without notice.

## What is covered

- Device discovery through the hOn appliance list.
- State reads through the hOn command/context endpoints.
- Capability-aware controls exposed by the device schema.
- REST polling for eventual state reconciliation.

## What is not promised

- Every Haier or Candy device family.
- Identical controls across models.
- Instant updates after using the physical remote.
- Stability if hOn changes authentication or response formats.

When reporting compatibility, include the Haier model and the capabilities shown by the
device, but redact account identifiers, MAC addresses, tokens, coordinates and credentials.
Recorded fixtures must be anonymized and reviewed before being committed.
