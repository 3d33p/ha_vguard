"""CLI: login → products → subscribe → decrypt → human-readable dashboard."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

from . import auth, nous, products
from .client import VGuardClient
from .parser import parse_decrypted_payload
from .report import write_field_report

# Repo root = parent of the installed/package directory (…/vguard_client/vguard_client → …/vguard_client)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_REPORT_NAME = "vguard_report.html"


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in {"1", "true", "on", "yes"}:
        return True
    if v in {"0", "false", "off", "no"}:
        return False
    raise argparse.ArgumentTypeError(f"expected ON/OFF, got {value!r}")


def _resolve_report_path(path: str | None) -> str | None:
    """Write relative report paths under the repo root, not the process cwd."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    return str(p)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="VGuard Smart 2.0 test client - login, list products, poll Nous subscribe",
    )
    p.add_argument("--email", default=os.environ.get("VGUARD_EMAIL"), help="Account email")
    p.add_argument(
        "--password",
        default=os.environ.get("VGUARD_PASSWORD"),
        help="Account password (or set VGUARD_PASSWORD)",
    )
    p.add_argument("--serial", help="Pick product by serialNumber (default: best match)")
    p.add_argument("--once", action="store_true", help="Single subscribe poll then exit")
    p.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Poll interval seconds (default: 3, or 6 when categoryId==2)",
    )
    p.add_argument("--raw", action="store_true", help="Also print full decrypted JSON")
    p.add_argument(
        "--max-polls",
        type=int,
        default=0,
        help="Stop after N polls in loop mode (0 = forever)",
    )
    p.add_argument(
        "--wake",
        action="store_true",
        default=False,
        help=(
            "DEPRECATED/unsafe: on empty cache, publish VG105:1 which unlocks the "
            "battery-type rear slider for ~30 min (not a harmless wake)"
        ),
    )
    p.add_argument(
        "--no-wake",
        action="store_false",
        dest="wake",
        help="Do not publish VG105:1 on empty cache (default)",
    )
    p.add_argument(
        "--wake-wait",
        type=float,
        default=4.0,
        help="Seconds between empty-cache subscribe retries (default 4)",
    )
    p.add_argument(
        "--retries",
        type=int,
        default=5,
        help="Subscribe retries when no payload yet (default 5)",
    )
    p.add_argument(
        "--unlock-battery-type",
        action="store_true",
        help="Publish VG105:1 to unlock rear battery-type slider for ~30 minutes",
    )
    p.add_argument(
        "--decrypt",
        choices=("auto", "always", "never"),
        default="auto",
        help="Payload crypto: auto skips AES when payload is already JSON (default)",
    )
    p.add_argument(
        "--report",
        default=_DEFAULT_REPORT_NAME,
        help=(
            "Write visual HTML field report (default: vguard_report.html under the repo root; "
            "relative paths are resolved from the repo root, not cwd)"
        ),
    )
    p.add_argument(
        "--no-report",
        action="store_true",
        help="Do not write the HTML field report",
    )
    p.add_argument(
        "--encrypt-publish",
        action="store_true",
        help="AES-encrypt publish payloads (default: plaintext, matching official Wi-Fi app)",
    )
    # Optional writers (publish then continue to poll)
    p.add_argument(
        "--set-power",
        type=_parse_bool,
        default=None,
        metavar="ON|OFF",
        help="Main inverter power button (VG094)",
    )
    p.add_argument("--set-holiday", type=_parse_bool, default=None, metavar="ON|OFF")
    p.add_argument("--set-turbo", type=_parse_bool, default=None, metavar="ON|OFF")
    p.add_argument("--set-appliance", type=_parse_bool, default=None, metavar="ON|OFF")
    p.add_argument("--set-extra-backup", type=_parse_bool, default=None, metavar="ON|OFF")
    p.add_argument(
        "--set-advance-battery-low-alarm",
        type=_parse_bool,
        default=None,
        metavar="ON|OFF",
        help="Advance Battery Low Alarm (Alarm settings)",
    )
    p.add_argument("--set-force-cut", type=_parse_bool, default=None, metavar="ON|OFF")
    p.add_argument(
        "--force-cut-minutes",
        type=int,
        default=60,
        help="Minutes for --set-force-cut ON: 30, 60, 120, 180, or 240 (default 60)",
    )
    p.add_argument(
        "--set-performance",
        type=int,
        default=None,
        metavar="1-7",
        help="Performance/backup linked level (1=max backup, 7=max performance)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow --set-performance on mains, or --set-daytime-load when Power "
            "Saver is not Max (device may ignore)"
        ),
    )
    p.add_argument(
        "--set-load-alarm",
        type=int,
        default=None,
        metavar="50|60|70|80|90|100",
        help="Overload alarm percent (app presets only)",
    )
    p.add_argument("--set-buzzer", type=_parse_bool, default=None, metavar="ON|OFF")
    p.add_argument(
        "--set-daytime-load",
        type=_parse_bool,
        default=None,
        metavar="ON|OFF",
        help="Requires Power Saver Max (hardware rear switch)",
    )
    p.add_argument(
        "--set-power-saver-max",
        type=_parse_bool,
        default=None,
        metavar="ON|OFF",
        help="Not supported over cloud — hardware rear switch only (will error)",
    )
    return p


def _command_preview(name: str, args: argparse.Namespace) -> list[str]:
    """Human-readable VG strings about to be published."""
    from . import commands

    cmds: list[str] = []
    if name == "power":
        cmds.append(commands.cmd_power(args.set_power))
    elif name == "holiday":
        cmds.append(commands.cmd_holiday_mode(args.set_holiday))
    elif name == "turbo":
        cmds.append(commands.cmd_turbo_charging(args.set_turbo))
    elif name == "appliance":
        cmds.append(commands.cmd_appliance_mode(args.set_appliance))
    elif name == "extra_backup":
        cmds.append(commands.cmd_extra_backup(args.set_extra_backup))
    elif name == "advance_battery_low_alarm":
        cmds.append(commands.cmd_advance_battery_low_alarm(args.set_advance_battery_low_alarm))
    elif name == "force_cut":
        if args.set_force_cut:
            cmds.append(commands.cmd_force_cut_duration(args.force_cut_minutes))
            cmds.append(commands.cmd_force_cut_enabled(True))
        else:
            cmds.append(commands.cmd_force_cut_duration(commands.FORCE_CUT_CLEAR_MINUTES))
            cmds.append(commands.cmd_force_cut_enabled(False))
    elif name == "performance":
        cmds.append(commands.cmd_performance_backup_level(args.set_performance))
    elif name == "load_alarm":
        cmds.append(commands.cmd_load_alarm_percent(args.set_load_alarm))
    elif name == "buzzer":
        cmds.append(commands.cmd_mains_changeover_buzzer(args.set_buzzer))
    elif name == "daytime_load":
        cmds.append(commands.cmd_day_time_load_usage(args.set_daytime_load))
    elif name == "unlock_battery_type":
        cmds.append(commands.cmd_unlock_battery_type())
    return cmds


def _apply_set_commands(client: VGuardClient, args: argparse.Namespace) -> None:
    planned: list[tuple[str, Any]] = []
    if args.set_power is not None:
        planned.append(("power", lambda: client.set_power(args.set_power)))
    if args.set_holiday is not None:
        planned.append(("holiday", lambda: client.set_holiday_mode(args.set_holiday)))
    if args.set_turbo is not None:
        planned.append(("turbo", lambda: client.set_turbo_charging(args.set_turbo)))
    if args.set_appliance is not None:
        planned.append(("appliance", lambda: client.set_appliance_mode(args.set_appliance)))
    if args.set_extra_backup is not None:
        planned.append(("extra_backup", lambda: client.set_extra_backup(args.set_extra_backup)))
    if args.set_advance_battery_low_alarm is not None:
        planned.append(
            (
                "advance_battery_low_alarm",
                lambda: client.set_advance_battery_low_alarm(
                    args.set_advance_battery_low_alarm
                ),
            )
        )
    if args.set_force_cut is not None:
        planned.append(
            (
                "force_cut",
                lambda: client.set_force_power_cut(
                    args.set_force_cut, minutes=args.force_cut_minutes
                ),
            )
        )
    if args.set_performance is not None:
        planned.append(
            (
                "performance",
                lambda: client.set_performance_backup_level(
                    args.set_performance, force=args.force
                ),
            )
        )
    if args.set_load_alarm is not None:
        planned.append(
            ("load_alarm", lambda: client.set_load_alarm_percent(args.set_load_alarm))
        )
    if args.set_buzzer is not None:
        planned.append(
            ("buzzer", lambda: client.set_mains_changeover_buzzer(args.set_buzzer))
        )
    if args.set_daytime_load is not None:
        planned.append(
            (
                "daytime_load",
                lambda: client.set_day_time_load_usage(
                    args.set_daytime_load, force=args.force
                ),
            )
        )
    if args.set_power_saver_max is not None:
        planned.append(
            ("power_saver", lambda: client.set_power_saver_max(args.set_power_saver_max))
        )
    if args.unlock_battery_type:
        planned.append(("unlock_battery_type", lambda: client.unlock_battery_type()))
    if not planned:
        return

    mode = "AES" if client.encrypt_publish else "plaintext"
    print(f"\n== Set commands ({mode} publish) ==")
    for name, runner in planned:
        preview = ", ".join(_command_preview(name, args))
        print(f"  {name}: sending {preview}")
        resp = runner()
        print(f"  {name}: {json.dumps(resp, default=str)[:400]}")
    print("waiting 8s for device to apply…")
    time.sleep(8.0)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.email or not args.password:
        print(
            "Error: provide --email/--password or VGUARD_EMAIL / VGUARD_PASSWORD",
            file=sys.stderr,
        )
        return 2

    http = requests.Session()
    client = VGuardClient(
        args.email,
        args.password,
        serial=args.serial,
        session=http,
        encrypt_publish=args.encrypt_publish,
    )

    print("== 1. Login ==")
    try:
        product = client.connect()
        session = client.session
    except (auth.AuthError, products.ProductsError) as exc:
        print(f"Login/products failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"OK accessToken length={len(session.access_token)} "
        f"refreshToken length={len(session.refresh_token)}"
    )

    print("\n== 2. Products ==")
    try:
        items = products.get_products(session.access_token, session=http)
    except products.ProductsError as exc:
        print(f"Products failed: {exc}", file=sys.stderr)
        return 1

    for i, item in enumerate(items):
        print(
            f"  [{i}] nick={item.product_nick_name!r} name={item.product_name!r} "
            f"serial={item.serial_number!r} type={item.product_type!r} "
            f"deviceCode={item.device_code!r} categoryId={item.category_id} "
            f"solar={item.is_solar} key/iv={'yes' if item.has_crypto else 'NO'}"
        )

    print(
        f"\nSelected: serial={product.serial_number} type={product.product_type} "
        f"deviceCode={product.device_code} interval={product.poll_interval_s}s"
    )
    if not product.has_crypto:
        print("WARNING: selected product has no key/iv — decrypt will fail", file=sys.stderr)

    try:
        _apply_set_commands(client, args)
    except Exception as exc:  # noqa: BLE001
        print(f"Set command failed: {exc}", file=sys.stderr)
        return 1

    interval = args.interval if args.interval is not None else product.poll_interval_s
    polls = 0

    print("\n== 3. Device info-status ==")
    try:
        info = nous.info_status(session.access_token, product.serial_number or "", session=http)
        print(json.dumps(info, indent=2)[:1500])
    except Exception as exc:  # noqa: BLE001
        print(f"info-status failed (continuing): {exc}", file=sys.stderr)

    print("\n== 4. Subscribe / decrypt / map ==")
    woke = False
    while True:
        polls += 1
        print(f"\n--- poll #{polls} ---")
        try:
            got_data = _poll_once(
                http,
                session,
                product,
                show_raw=args.raw,
                wake=args.wake and not woke,
                wake_wait=args.wake_wait,
                retries=args.retries,
                decrypt_mode=args.decrypt,
                encrypt_publish=args.encrypt_publish,
                report_path=None if args.no_report else _resolve_report_path(args.report),
                poll=polls,
            )
            if args.wake:
                woke = True
            if not got_data and args.once:
                print(
                    "\nHint: Open the inverter in the VGuard app once so it is online, "
                    "confirm Wi‑Fi, then re-run. Cloud only returns packets the device has pushed.",
                    file=sys.stderr,
                )
        except auth.AuthError as exc:
            print(f"Auth error, trying refresh: {exc}", file=sys.stderr)
            try:
                session.access_token = auth.refresh_token(session.refresh_token, session=http)
                print("Token refreshed")
                _poll_once(
                    http,
                    session,
                    product,
                    show_raw=args.raw,
                    wake=args.wake,
                    wake_wait=args.wake_wait,
                    retries=args.retries,
                    decrypt_mode=args.decrypt,
                    encrypt_publish=args.encrypt_publish,
                    report_path=None if args.no_report else _resolve_report_path(args.report),
                    poll=polls,
                )
            except Exception as refresh_exc:  # noqa: BLE001
                print(f"Refresh/retry failed: {refresh_exc}", file=sys.stderr)
                return 1
        except Exception as exc:  # noqa: BLE001
            print(f"Poll error: {exc}", file=sys.stderr)

        if args.once:
            break
        if args.max_polls and polls >= args.max_polls:
            break
        time.sleep(interval)

    return 0


def _poll_once(
    http: requests.Session,
    session: auth.Session,
    product: products.Product,
    *,
    show_raw: bool,
    wake: bool = True,
    wake_wait: float = 4.0,
    retries: int = 5,
    decrypt_mode: str = "auto",
    encrypt_publish: bool = False,
    report_path: str | None = None,
    poll: int = 1,
) -> bool:
    """Returns True if a decryptable payload was processed."""
    force_decrypt: bool | None
    if decrypt_mode == "always":
        force_decrypt = True
    elif decrypt_mode == "never":
        force_decrypt = False
    else:
        force_decrypt = None

    sub = nous.subscribe(session.access_token, product, session=http)
    status = sub.get("status")
    message = sub.get("message")
    data = sub.get("data") or {}
    print(f"subscribe status={status} message={message!r} type={data.get('type')!r}")

    if not nous.has_payload(sub) and wake:
        print(
            f"WARNING: empty cache — publishing {nous.BATTERY_TYPE_UNLOCK_COMMAND!r} "
            f"(this UNLOCKS battery-type rear slider for ~30 min; topic "
            f"apps/{product.product_type}/{product.device_code}/{product.serial_number})"
        )
        pub = nous.publish(
            session.access_token,
            product,
            nous.BATTERY_TYPE_UNLOCK_COMMAND,
            encrypt_payload=encrypt_publish,
            session=http,
        )
        print(f"publish response: {json.dumps(pub)[:500]}")
        for attempt in range(1, retries + 1):
            print(f"waiting {wake_wait}s then subscribe retry {attempt}/{retries}…")
            time.sleep(wake_wait)
            sub = nous.subscribe(session.access_token, product, session=http)
            status = sub.get("status")
            message = sub.get("message")
            data = sub.get("data") or {}
            print(f"subscribe status={status} message={message!r} type={data.get('type')!r}")
            if nous.has_payload(sub):
                break
    elif not nous.has_payload(sub):
        for attempt in range(1, retries + 1):
            print(f"empty cache — waiting {wake_wait}s then subscribe retry {attempt}/{retries}…")
            time.sleep(wake_wait)
            sub = nous.subscribe(session.access_token, product, session=http)
            status = sub.get("status")
            message = sub.get("message")
            data = sub.get("data") or {}
            print(f"subscribe status={status} message={message!r} type={data.get('type')!r}")
            if nous.has_payload(sub):
                break

    if not nous.has_payload(sub):
        print("No payload in response (device offline or empty). Full response:")
        print(json.dumps(sub, indent=2)[:2000])
        return False

    raw_payload = (sub.get("data") or {}).get("payload")
    looks_plain = isinstance(raw_payload, str) and raw_payload.lstrip().startswith("{")
    print(
        f"payload handling: mode={decrypt_mode} "
        f"({'plaintext JSON' if looks_plain and decrypt_mode != 'always' else 'AES decrypt'})"
    )

    decrypted = nous.decrypt_subscribe_payload(
        product, sub, force_decrypt=force_decrypt
    )
    if decrypted is None:
        print("Decrypt returned None")
        return False

    preview = decrypted if len(decrypted) < 400 else decrypted[:400] + "…"
    print(f"decrypted preview: {preview}")
    if show_raw:
        print("decrypted JSON:")
        try:
            print(json.dumps(json.loads(decrypted), indent=2)[:8000])
        except json.JSONDecodeError:
            print(decrypted[:8000])

    parsed = parse_decrypted_payload(decrypted, is_solar=product.is_solar)
    if not parsed.get("ok"):
        print(f"Parse failed: {parsed}")
        return False

    dashboard: dict[str, Any] = parsed["dashboard"]
    print("\nHuman-readable dashboard:")
    interesting = [
        "is_power_on",
        "mains_status_label",
        "load_status_label",
        "mains_voltage",
        "output_voltage",
        "battery_percentage",
        "battery_voltage",
        "load_current",
        "load_watts",
        "inverter_efficiency_percent",
        "charging_current",
        "solar_current",
        "solar_status_label",
        "is_solar_available",
        "is_solar_product",
        "solar_alarm",
        "load_percentage",
        "power_mode_label",
        "charging_mode_label",
        "battery_type_label",
        "power_saver_mode_label",
        "battery_lock_status",
        "battery_type_change_locked",
        "battery_type_unlock_minutes_remaining",
        "battery_type_app_change_enabled",
        "is_day_time_load_usage_enabled",
        "is_holiday_mode_enabled",
        "is_mains_force_cut_enabled",
        "main_force_cut_time",
        "is_turbo_charging",
        "is_turbo_charging_switch_on",
        "is_turbo_charging_enable_allowed",
        "turbo_charging_block_reason",
        "is_appliance_mode_enable_allowed",
        "appliance_mode_block_reason",
        "appliance_mode_ups_warning",
        "is_extra_backup_eligible",
        "is_extra_backup_enable_allowed",
        "extra_backup_block_reason",

        "performance_backup_level",
        "performance_slider",
        "backup_slider",
        "is_appliance_mode_enabled",
        "is_extra_backup_enabled",
        "load_alarm_percent",
        "low_battery_alarm",
        "is_advance_battery_low_alarm_enabled",
        "mains_changeover_buzzer_on",
        "power_cut_today_count",
        "power_cut_today_duration",
        "power_cut_trends_summary",
        "ssid_name",
        "wifi_rssi",
        "wifi_signal_quality",
        "device_time",
    ]
    for key in interesting:
        if key in dashboard:
            print(f"  {key}: {dashboard[key]}")

    print("\nFull mapped fields:")
    print(json.dumps(dashboard, indent=2, default=str))

    unmapped = parsed.get("unmapped_vg_keys") or []
    print(f"\nUnmapped VG keys ({len(unmapped)}): {', '.join(unmapped) if unmapped else '(none)'}")

    if report_path:
        out = write_field_report(
            out_path=report_path,
            product_name=product.product_nick_name or product.product_name or "VGuard",
            serial=product.serial_number or "",
            poll=poll,
            dashboard=dashboard,
            vg029=parsed.get("vg029") or {},
            unmapped_keys=list(unmapped),
        )
        print(f"\nVisual report written: {out.resolve()}")

    return True


if __name__ == "__main__":
    raise SystemExit(main())
