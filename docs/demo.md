# Five-minute demo

## Start

Follow the [README installation steps](../README.md#try-the-demo), then run:

```sh
python -m scripts.demo
```

Open `http://127.0.0.1:8788`. The header says **DEMO · SIMULATED DEVICES**. This runner
always supplies `MockDriver`, temporary storage and an in-memory key. It binds to loopback
and signs in the local browser through the existing trusted-network session mechanism.
It cannot access real appliances. Stop it with Ctrl+C; demo data is removed on exit.

## Walkthrough

1. **Start with context.** Show the two simulated rooms and distinguish target from
   room temperature. Narrow the window to demonstrate the mobile selector.
2. **Change a setting.** Adjust temperature, switch modes and toggle power. Explain
   the pending/confirmed state. The mock driver is fast, so do not present its response
   time as measured hOn performance.
3. **Show secondary controls.** Open **More** for swing and advertised advanced options.
4. **Create a timer.** Open **Timer**, choose **After a delay** or **At a set time** and
   save it. Edit the time, then delete it. This uses the real application scheduler
   and persistence layer with a simulated appliance.
5. **Explain the boundary.** The UI/API/scheduler run locally; the real driver requires
   hOn for device communication. Local SSE is not a vendor realtime feed.

The demo deliberately resets on exit. Use the normal Docker deployment and its persistent
volume when evaluating restart behavior. Never use real appliance credentials during a
screen-shared sign-in demonstration.

## Screenshots

[Desktop](images/dashboard-desktop.png) and [mobile](images/dashboard-mobile.png) previews
are screenshots of this application with simulated devices, not product mockups.
