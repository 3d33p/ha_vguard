# Home Assistant Integration for V-Guard Smart

Unofficial integration for **V-Guard Smart 2.0** inverters (cloud account). Not affiliated with V-Guard.

## Installation

### Method 1: Using [HACS](https://hacs.xyz)

1. Open this repository in your browser and copy its URL from the address bar
   (for example `https://github.com/<owner>/<repo>`).
2. Open your Home Assistant UI.
3. Go to **HACS**.
4. Open the three-dot menu and select **Custom repositories**.
5. Add:
   - **URL:** paste the repository URL you copied
   - **Category:** Integration
6. Click **Add**.
7. Search for **V-Guard Smart** in HACS and download it.
8. Restart Home Assistant.

### Method 2: Manual Installation

1. Open your Home Assistant config directory (where `configuration.yaml` lives).
2. Create `custom_components` if it does not exist.
3. Copy the `vguard` folder from [`custom_components/vguard/`](custom_components/vguard/) in this repository into `custom_components/vguard/`.
4. Restart Home Assistant.

## Configuration

1. Open **Settings → Devices & services → Add integration**.
2. Search for **V-Guard**.
3. Sign in with your V-Guard Smart app email and password.
4. Optionally enter a serial number if the account has more than one device.

## Lovelace — Power Cut Trends (ApexCharts)

The **Power Cut Trends (Today)** sensor shows a one-line summary on the device page. For a 7-day bar chart like the app:

1. In **HACS → Frontend**, download **[ApexCharts Card](https://github.com/RomRider/apexcharts-card)** and restart Home Assistant.
2. Open **Settings → Devices & services → V-Guard** → your inverter device → **Power Cut Trends (Today)**.
3. Copy that entity’s `entity_id` (for example `sensor.v_guard_smart_inverter_power_cut_trends`).
4. Open a dashboard → **Edit** → **Add card** → **Manual**.
5. Paste the contents of [`lovelace/power_cut_trends_apexcharts.yaml`](lovelace/power_cut_trends_apexcharts.yaml).
6. Replace **both** `SENSOR_ENTITY` placeholders with the `entity_id` you copied.
7. Save the card.

Without ApexCharts, you can use the plain markdown table card instead: [`lovelace/power_cut_trends.yaml`](lovelace/power_cut_trends.yaml).

## Notes

- Uses **cloud polling** (default **60s**). Press **Live Updates** or change a control for temporary **6s** polling (about 60s), then it reverts. **Poll Interval** ≥30s sticks; under 30s is temporary.
- **Online** (diagnostic binary sensor) is on when the cloud subscribe API returns a device payload, and off when the payload is empty (device offline / empty cache). Other entities keep their last readings instead of flipping to Unavailable on a single miss.
- Sessions are cached (access/refresh tokens + a stable phone-like FCM id) so restarts can refresh without a full password login.
- Brand icons ship in `custom_components/vguard/brand/` (Home Assistant 2026.3+ local brands).
- Inverter mode, charging mode, power saver, and battery type are **sensors only** — they follow the physical switches; this integration does not write them.
- After updating via HACS or manually, **restart Home Assistant**. GitHub **Releases** (not just tags) give HACS a proper version number.

## Logs

```yaml
logger:
  logs:
    custom_components.vguard: debug
```

## Disclaimer

This project is an unofficial, community-maintained Home Assistant integration. It is **not affiliated with, endorsed by, or connected to V-Guard Industries Ltd.** (or any related company) in any way. V-Guard and related names and logos are trademarks of their respective owners. Use at your own risk.
