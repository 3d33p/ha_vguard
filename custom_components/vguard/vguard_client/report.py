"""Write a visual HTML field report for easier mapping checks."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mappings import BASE_FIELD_SPECS, SOLAR_FIELD_SPECS

# field name → VG key(s) that feed it
_FIELD_TO_VG: dict[str, list[str]] = {}
for vg_key, name, _ in BASE_FIELD_SPECS + SOLAR_FIELD_SPECS:
    _FIELD_TO_VG.setdefault(name, []).append(vg_key)
_FIELD_TO_VG.setdefault("power_cut_today_count", []).extend(["VG070"])
_FIELD_TO_VG.setdefault("power_cut_today_duration", []).extend(["VG069"])
_FIELD_TO_VG.setdefault("load_alarm_percent", []).extend(["VG050"])
_FIELD_TO_VG.setdefault("load_alarm_percent_raw", []).extend(["VG050"])
_FIELD_TO_VG.setdefault("low_battery_alarm", []).extend(["VG071"])
_FIELD_TO_VG.setdefault("mains_changeover_buzzer_on", []).extend(["VG034"])


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _load_prev(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_prev(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _diff_rows(
    prev: dict[str, Any] | None,
    current: dict[str, Any],
) -> list[tuple[str, Any, Any]]:
    if not prev:
        return []
    keys = sorted(set(prev) | set(current))
    out: list[tuple[str, Any, Any]] = []
    for key in keys:
        a, b = prev.get(key), current.get(key)
        if a != b:
            out.append((key, a, b))
    return out


def write_field_report(
    *,
    out_path: str | Path,
    product_name: str,
    serial: str,
    poll: int,
    dashboard: dict[str, Any],
    vg029: dict[str, Any],
    unmapped_keys: list[str],
) -> Path:
    """Write HTML report; return path written."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state_path = path.with_suffix(".prev.json")

    prev = _load_prev(state_path)
    prev_vg = (prev or {}).get("vg029") if isinstance(prev, dict) else None
    prev_dash = (prev or {}).get("dashboard") if isinstance(prev, dict) else None
    vg_diffs = _diff_rows(prev_vg if isinstance(prev_vg, dict) else None, vg029)
    dash_diffs = _diff_rows(prev_dash if isinstance(prev_dash, dict) else None, dashboard)

    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    device_time = dashboard.get("device_time") or "—"

    identified_rows: list[str] = []
    for name in sorted(dashboard.keys()):
        value = dashboard[name]
        vg_keys = _FIELD_TO_VG.get(name, [])
        raw_parts = []
        for vg in vg_keys:
            if vg in vg029:
                raw_parts.append(f"{vg}={vg029[vg]}")
        raw = ", ".join(raw_parts) if raw_parts else ("derived" if not vg_keys else "—")
        vg_label = ", ".join(vg_keys) if vg_keys else "—"
        changed = " class=\"changed\"" if any(d[0] == name for d in dash_diffs) else ""
        identified_rows.append(
            f"<tr{changed}><td>{_esc(name)}</td><td class=\"mono\">{_esc(vg_label)}</td>"
            f"<td class=\"val\">{_esc(_fmt(value))}</td><td class=\"mono muted\">{_esc(raw)}</td></tr>"
        )

    unidentified_rows: list[str] = []
    for key in sorted(unmapped_keys):
        raw = vg029.get(key, "")
        changed = " class=\"changed\"" if any(d[0] == key for d in vg_diffs) else ""
        unidentified_rows.append(
            f"<tr{changed}><td class=\"mono\">{_esc(key)}</td>"
            f"<td class=\"val\">{_esc(_fmt(raw))}</td></tr>"
        )

    # Also list all mapped VG keys present with raw values
    mapped_raw_rows: list[str] = []
    mapped_keys = sorted(k for k in vg029 if k not in unmapped_keys)
    for key in mapped_keys:
        changed = " class=\"changed\"" if any(d[0] == key for d in vg_diffs) else ""
        mapped_raw_rows.append(
            f"<tr{changed}><td class=\"mono\">{_esc(key)}</td>"
            f"<td class=\"val\">{_esc(_fmt(vg029.get(key)))}</td></tr>"
        )

    diff_rows_html: list[str] = []
    for key, old, new in vg_diffs:
        kind = "unidentified" if key in unmapped_keys else "identified"
        diff_rows_html.append(
            f"<tr class=\"changed\"><td class=\"mono\">{_esc(key)}</td>"
            f"<td>{_esc(kind)}</td>"
            f"<td class=\"old\">{_esc(_fmt(old))}</td>"
            f"<td class=\"new\">{_esc(_fmt(new))}</td></tr>"
        )

    status_bits = [
        ("Mains", dashboard.get("mains_status_label")),
        ("Load", dashboard.get("load_status_label")),
        ("Battery", f"{dashboard.get('battery_percentage')}%"),
        ("Load %", dashboard.get("load_percentage")),
        ("Mains V", dashboard.get("mains_voltage")),
        ("Battery V", dashboard.get("battery_voltage")),
        ("Power", "ON" if dashboard.get("is_power_on") else "OFF"),
    ]
    cards = "".join(
        f"<div class=\"card\"><div class=\"k\">{_esc(k)}</div><div class=\"v\">{_esc(_fmt(v))}</div></div>"
        for k, v in status_bits
    )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>VGuard field report — {_esc(product_name)}</title>
<style>
  :root {{
    --bg: #0f1419;
    --panel: #1a222c;
    --line: #2a3542;
    --text: #e8eef4;
    --muted: #8b9aab;
    --ok: #3d9a6a;
    --warn: #c4a035;
    --accent: #4a9fd8;
    --changed: #3a2f14;
    --changed-border: #c4a035;
    --old: #c45c5c;
    --new: #3d9a6a;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px;
    font-family: "Segoe UI", "IBM Plex Sans", sans-serif;
    background: radial-gradient(1200px 600px at 10% -10%, #1a2a38 0%, var(--bg) 55%);
    color: var(--text);
    line-height: 1.45;
  }}
  h1 {{ font-size: 1.45rem; margin: 0 0 4px; font-weight: 650; }}
  h2 {{ font-size: 1.05rem; margin: 28px 0 10px; color: var(--accent); font-weight: 600; }}
  .meta {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 18px; }}
  .meta strong {{ color: var(--text); font-weight: 600; }}
  .cards {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 10px; margin: 16px 0 8px;
  }}
  .card {{
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: 12px 14px;
  }}
  .card .k {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }}
  .card .v {{ font-size: 1.15rem; font-weight: 650; margin-top: 4px; }}
  .stats {{ display: flex; gap: 14px; flex-wrap: wrap; margin: 8px 0 0; color: var(--muted); font-size: 0.88rem; }}
  .stats span {{ background: var(--panel); border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; }}
  .stats .n {{ color: var(--text); font-weight: 600; }}
  table {{
    width: 100%; border-collapse: collapse; background: var(--panel);
    border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
    font-size: 0.9rem;
  }}
  th, td {{ padding: 8px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
  th {{ background: #121920; color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; position: sticky; top: 0; }}
  tr:last-child td {{ border-bottom: none; }}
  tr.changed {{ background: var(--changed); outline: 1px solid var(--changed-border); }}
  .mono {{ font-family: "Cascadia Code", "Consolas", monospace; font-size: 0.86rem; }}
  .muted {{ color: var(--muted); }}
  .val {{ font-weight: 600; }}
  .old {{ color: var(--old); text-decoration: line-through; }}
  .new {{ color: var(--new); font-weight: 650; }}
  .hint {{ color: var(--muted); font-size: 0.85rem; margin: 0 0 10px; }}
  .badge {{
    display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 650;
  }}
  .badge.ok {{ background: rgba(61,154,106,.2); color: #7fd4a8; }}
  .badge.warn {{ background: rgba(196,160,53,.2); color: #e6c86a; }}
  details {{ margin-top: 8px; }}
  summary {{ cursor: pointer; color: var(--muted); }}
  pre {{
    background: #121920; border: 1px solid var(--line); border-radius: 10px;
    padding: 12px; overflow: auto; font-size: 0.8rem; max-height: 420px;
  }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>{_esc(product_name)} <span class="badge ok">field report</span></h1>
  <div class="meta">
    Serial <strong>{_esc(serial)}</strong> · Poll <strong>#{poll}</strong> · Written <strong>{_esc(now)}</strong><br/>
    Device time <strong>{_esc(device_time)}</strong>
  </div>

  <div class="cards">{cards}</div>
  <div class="stats">
    <span>Identified fields <span class="n">{len(dashboard)}</span></span>
    <span>Unidentified VG keys <span class="n">{len(unmapped_keys)}</span></span>
    <span>VG029 keys total <span class="n">{len(vg029)}</span></span>
    <span>Changed since last poll <span class="n">{len(vg_diffs)}</span></span>
  </div>

  <h2>Changed since last poll <span class="badge {"warn" if vg_diffs else "ok"}">{len(vg_diffs)}</span></h2>
  <p class="hint">Yellow rows = changed. Use this when toggling Smart switches.</p>
  {"<table><thead><tr><th>Key</th><th>Kind</th><th>Before</th><th>After</th></tr></thead><tbody>" + "".join(diff_rows_html) + "</tbody></table>" if diff_rows_html else "<p class='hint'>No previous poll yet, or nothing changed.</p>"}

  <h2>Unidentified VG keys <span class="badge warn">{len(unmapped_keys)}</span></h2>
  <p class="hint">Raw packet keys not yet bound to a dashboard field. Highlighted if changed.</p>
  <table>
    <thead><tr><th>VG key</th><th>Raw value</th></tr></thead>
    <tbody>
      {"".join(unidentified_rows) if unidentified_rows else "<tr><td colspan='2'>None</td></tr>"}
    </tbody>
  </table>

  <h2>Identified fields <span class="badge ok">{len(dashboard)}</span></h2>
  <p class="hint">Mapped dashboard fields with source VG key(s) and raw value(s).</p>
  <table>
    <thead><tr><th>Field</th><th>VG key(s)</th><th>Mapped value</th><th>Raw</th></tr></thead>
    <tbody>
      {"".join(identified_rows)}
    </tbody>
  </table>

  <h2>Mapped VG raw values</h2>
  <table>
    <thead><tr><th>VG key</th><th>Raw value</th></tr></thead>
    <tbody>
      {"".join(mapped_raw_rows)}
    </tbody>
  </table>

  <h2>Full VG029 JSON</h2>
  <details>
    <summary>Show raw JSON</summary>
    <pre>{_esc(json.dumps(vg029, indent=2, default=str))}</pre>
  </details>
</div>
</body>
</html>
"""

    path.write_text(doc, encoding="utf-8")
    _save_prev(
        state_path,
        {
            "poll": poll,
            "written_at": now,
            "dashboard": dashboard,
            "vg029": vg029,
            "unmapped_keys": unmapped_keys,
        },
    )
    return path
