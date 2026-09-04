# Optional Google Assistant bridge

The recommended route is an optional Home Assistant bridge:

1. Keep Haier Control as the source of device control and timers.
2. Create scoped local API tokens for the bridge.
3. Model REST commands/sensors in Home Assistant or a small custom adapter.
4. Expose only the selected climate entities through the existing Google Home path.

This preserves the core's independence: if Home Assistant is stopped, Haier Control and
its timers continue to work. No Google project, cloud registration, public webhook, or
external tunnel is created by this repository.

For a future direct adapter, use `/api/v1/devices`, `/commands`, and `/timers`; do not let a
voice bridge read the encrypted hOn session. Give it only the scopes it needs.
