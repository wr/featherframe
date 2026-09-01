# Detection source troubleshooting

The **Test connection** button on the config page tells you whether Featherframe
can reach the detection source you've entered. If it reports a problem, find your
source below.

Featherframe never writes to your detector — every source is read-only — so a
failed test can't harm BirdNET.

---

## BirdNET-Go (API)

Featherframe polls BirdNET-Go's REST API.

- **Not reachable** — the URL is wrong or BirdNET-Go isn't serving the API.
  - Confirm the base URL (scheme + host + port), e.g. `http://10.0.1.50:8080`.
    Don't include a path.
  - From the Featherframe host, check it directly:
    `curl http://<host>:<port>/api/v2/detections?numResults=1`
  - Make sure BirdNET-Go and Featherframe are on the same network and no
    firewall blocks the port.
- **Connected — no detections yet** — the API works but hasn't logged a bird.
  Wait for a detection, or lower BirdNET-Go's confidence threshold.

## BirdNET-Pi (Apprise push)

BirdNET-Pi *pushes* each detection to Featherframe's webhook; there's nothing to
poll, so the test just reports how many have arrived.

- **0 received** — BirdNET-Pi hasn't posted yet.
  - In BirdNET-Pi, open **Tools → Settings → Notifications** and confirm the
    Apprise URL and JSON body match exactly what the config page shows (use the
    copy buttons).
  - If you set a **shared secret**, the URL must end with `/<secret>`.
  - Trigger a test notification from BirdNET-Pi, or wait for the next detection.
  - Both devices must be on the same LAN — the webhook is not exposed to the
    internet.

## BirdWeather (station)

Featherframe polls the BirdWeather cloud API for your station.

- **Not reachable** — the station ID/token is wrong, or the BirdWeather API is
  unreachable.
  - Paste your station URL from <https://app.birdweather.com/stations> (or just
    the ID); Featherframe extracts the ID for you.
  - The Featherframe host needs outbound internet access for this source.

## Custom (local BirdNET-Pi database)

Featherframe reads a BirdNET-Pi SQLite database directly (read-only).

- **Not reachable** — the path is wrong or the file isn't readable.
  - Default is `~/BirdNET-Pi/scripts/birds.db`.
  - The path is on the **Featherframe host**, not a remote machine. If BirdNET-Pi
    runs elsewhere, use the Apprise or BirdNET-Go source instead.
  - Confirm the file exists and the service user can read it:
    `ls -l <path>`

---

## Still stuck?

Open an issue at <https://github.com/wr/featherframe/issues> with the exact error
text from the Test button and which source you're using.
