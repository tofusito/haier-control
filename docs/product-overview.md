# Product overview

## A small dashboard for a frequent task

Haier Control began with a customer need: checking and changing the climate of a room
should be a short, predictable interaction. The project focuses on air conditioning rather
than the wider range of appliance journeys served by hOn.

The interface prioritizes room selection, target and room temperatures, power, mode and
fan speed. Secondary controls remain available in a separate sheet. Color helps distinguish
operating modes, while text and accessibility labels carry the same information.

## Design decisions worth evaluating

- **Context before controls.** Room state is visible before opening settings.
- **Feedback during cloud latency.** An immediate local change is shown as pending;
  a rejected or timed-out request rolls back rather than silently implying success.
- **Capability-driven controls.** The appliance schema determines available options.
- **A small mobile surface.** A room selector keeps the current card in focus, while
  desktop can show two cards together.
- **Server-side timers.** Closing the browser does not stop a scheduled timer. Execution
  still depends on a running server and, for real devices, hOn availability.

## Technical scope

The app hosts its UI, API, SQLite database and scheduler locally. Its cloud driver
communicates with the private hOn API. It does not provide offline control of real
appliances. The mock driver provides a repeatable demonstration without an hOn account.

Authentication and local access are documented in the [security model](security.md).
An accepted cloud request is not proof that a physical unit has completed the action.
REST state reads reconcile application state; vendor MQTT events and continuous
background polling are not implemented.

## Evidence and open questions

The repository includes tests for authentication flows, cloud response parsing, command
payloads, persistence, timers and optimistic UI behavior. Redacted fixtures preserve
observed response structure. They are not a certification of every device family.

There are no published usability-study results, measured performance comparisons with
hOn or independent security-audit findings. Wider model compatibility, accessibility
evaluation and user testing remain open work.

## Topics for a product discussion

1. Which everyday tasks cause the most friction for AC users?
2. Which elements of a focused room dashboard could fit the hOn experience?
3. What supported integration or authentication route could serve independent developers?
4. What evidence would make a broader evaluation useful to the product team?

This is an independent prototype shared for evaluation and feedback. Any collaboration
or official integration would require a separate discussion with the relevant teams.
