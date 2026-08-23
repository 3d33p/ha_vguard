"""High-level client for Home Assistant and other integrations."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from . import auth, commands, nous, products
from .auth import DeviceIdentity, generate_fcm_token
from .parser import parse_decrypted_payload

_LOGGER = logging.getLogger(__name__)


class VGuardError(RuntimeError):
    pass


@dataclass
class DashboardSnapshot:
    """Result of one successful subscribe + parse."""

    dashboard: dict[str, Any]
    vg029: dict[str, Any]
    unmapped_vg_keys: list[str]
    raw_subscribe: dict[str, Any]
    decrypted: str


class VGuardClient:
    """Login, poll dashboard, and publish device commands."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        serial: str | None = None,
        session: requests.Session | None = None,
        encrypt_publish: bool = False,
        fcm_token: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        on_session_update: Callable[[auth.Session], None] | None = None,
        platform_model: str = auth.DEFAULT_PLATFORM_MODEL,
        platform_version: str = auth.DEFAULT_PLATFORM_VERSION,
    ) -> None:
        self._email = email
        self._password = password
        self._serial = serial
        self._http = session or requests.Session()
        self._auth: auth.Session | None = None
        self._product: products.Product | None = None
        # Match official Wi‑Fi skipEncryption=true (plaintext VG commands).
        self.encrypt_publish = encrypt_publish
        self._on_session_update = on_session_update
        self._cached_access = access_token
        self._cached_refresh = refresh_token
        self._identity = DeviceIdentity(
            fcm_token=fcm_token or generate_fcm_token(),
            platform_model=platform_model,
            platform_version=platform_version,
        )

    @property
    def identity(self) -> DeviceIdentity:
        return self._identity

    @property
    def fcm_token(self) -> str:
        return self._identity.fcm_token

    @property
    def session(self) -> auth.Session:
        if self._auth is None:
            raise VGuardError("Not connected — call connect() first")
        return self._auth

    @property
    def product(self) -> products.Product:
        if self._product is None:
            raise VGuardError("Not connected — call connect() first")
        return self._product

    @property
    def access_token(self) -> str:
        if self._auth is None:
            raise VGuardError("Not connected — call connect() first")
        return self._auth.access_token

    def _notify_session(self) -> None:
        if self._auth is not None and self._on_session_update is not None:
            self._on_session_update(self._auth)

    def _select_product(self) -> products.Product:
        assert self._auth is not None
        items = products.get_products(
            self._auth.access_token,
            identity=self._identity,
            session=self._http,
        )
        self._product = products.pick_product(items, serial=self._serial)
        return self._product

    def connect(self) -> products.Product:
        """Restore cached tokens when possible; otherwise password login."""
        if self._cached_access and self._cached_refresh:
            self._auth = auth.Session(
                access_token=self._cached_access,
                refresh_token=self._cached_refresh,
            )
            try:
                product = self._select_product()
                _LOGGER.debug("Connected with cached access token")
                self._notify_session()
                return product
            except auth.AuthError:
                _LOGGER.debug("Cached access token rejected; trying refresh")
                try:
                    self.refresh_access_token()
                    product = self._select_product()
                    _LOGGER.debug("Connected after refresh-token reuse")
                    return product
                except (auth.AuthError, VGuardError) as exc:
                    _LOGGER.info("Token restore failed (%s); falling back to login", exc)
                    self._auth = None

        self._auth = auth.login(
            self._email,
            self._password,
            identity=self._identity,
            session=self._http,
        )
        product = self._select_product()
        self._notify_session()
        return product

    def refresh_access_token(self) -> None:
        if self._auth is None:
            raise VGuardError("Not connected")
        self._auth.access_token = auth.refresh_token(
            self._auth.refresh_token,
            identity=self._identity,
            session=self._http,
        )
        self._notify_session()

    def _with_auth_retry(self, fn):  # type: ignore[no-untyped-def]
        try:
            return fn()
        except auth.AuthError:
            self.refresh_access_token()
            return fn()

    def publish(self, command: str, *, encrypt: bool | None = None) -> dict[str, Any]:
        """Publish a raw VG command string to the device topic.

        Defaults to plaintext (``encrypt_publish=False``). Pass ``encrypt=True``
        only for devices that require AES payloads.
        """
        use_encrypt = self.encrypt_publish if encrypt is None else encrypt

        def _do() -> dict[str, Any]:
            return nous.publish(
                self.access_token,
                self.product,
                command,
                encrypt_payload=use_encrypt,
                identity=self._identity,
                session=self._http,
            )

        return self._with_auth_retry(_do)

    def unlock_battery_type(self) -> dict[str, Any]:
        """Unlock rear battery-type slider for ~30 minutes (VG105:1)."""
        return self.publish(commands.cmd_unlock_battery_type())

    def wake(self) -> dict[str, Any]:
        """Deprecated: VG105:1 unlocks battery type; prefer unlock_battery_type()."""
        return self.unlock_battery_type()

    def get_dashboard(
        self,
        *,
        wake: bool = False,
        wake_wait: float = 4.0,
        retries: int = 5,
        decrypt_mode: str = "auto",
    ) -> DashboardSnapshot:
        """Subscribe, decrypt, and map VG029 → dashboard.

        ``wake`` is deprecated and off by default. If enabled it publishes
        VG105:1, which unlocks the battery-type rear slider for ~30 minutes —
        it is not a harmless cache refresh.
        """
        force_decrypt: bool | None
        if decrypt_mode == "always":
            force_decrypt = True
        elif decrypt_mode == "never":
            force_decrypt = False
        else:
            force_decrypt = None

        def _subscribe() -> dict[str, Any]:
            return nous.subscribe(
                self.access_token,
                self.product,
                identity=self._identity,
                session=self._http,
            )

        sub = self._with_auth_retry(_subscribe)
        if not nous.has_payload(sub):
            if wake:
                self.unlock_battery_type()
            for _ in range(retries):
                time.sleep(wake_wait if wake else 2.0)
                sub = self._with_auth_retry(_subscribe)
                if nous.has_payload(sub):
                    break

        if not nous.has_payload(sub):
            raise VGuardError("No device payload (offline or empty cache)")

        decrypted = nous.decrypt_subscribe_payload(
            self.product, sub, force_decrypt=force_decrypt
        )
        if not decrypted:
            raise VGuardError("Decrypt returned empty payload")

        parsed = parse_decrypted_payload(decrypted, is_solar=self.product.is_solar)
        if not parsed.get("ok"):
            raise VGuardError(f"Parse failed: {parsed}")

        return DashboardSnapshot(
            dashboard=parsed["dashboard"],
            vg029=parsed["vg029"],
            unmapped_vg_keys=list(parsed.get("unmapped_vg_keys") or []),
            raw_subscribe=sub,
            decrypted=decrypted,
        )

    # ---- Convenience writers (Smart / Alarm settings) ----

    def set_power(self, enabled: bool) -> dict[str, Any]:
        """Main inverter power button (VG094)."""
        return self.publish(commands.cmd_power(enabled))

    def set_holiday_mode(self, enabled: bool) -> dict[str, Any]:
        return self.publish(commands.cmd_holiday_mode(enabled))

    def set_turbo_charging(self, enabled: bool) -> dict[str, Any]:
        return self.publish(commands.cmd_turbo_charging(enabled))

    def set_appliance_mode(self, enabled: bool) -> dict[str, Any]:
        return self.publish(commands.cmd_appliance_mode(enabled))

    def set_power_mode(self, mode: int) -> dict[str, Any]:
        """Inverter mode (VG021): 0=Normal, 1=UPS, 2=Equipment."""
        return self.publish(commands.cmd_power_mode(mode))

    def set_extra_backup(self, enabled: bool) -> dict[str, Any]:
        """Extra backup (VG072). App only allows this after low battery."""
        return self.publish(commands.cmd_extra_backup(enabled))

    def set_advance_battery_low_alarm(self, enabled: bool) -> dict[str, Any]:
        return self.publish(commands.cmd_advance_battery_low_alarm(enabled))

    def set_force_power_cut(
        self,
        enabled: bool,
        *,
        minutes: int = 60,
        settle_s: float = 1.0,
    ) -> list[dict[str, Any]]:
        """Enable or clear mains force cut.

        Enable: ``VG037:{minutes}`` with app presets 30/60/120/180/240, then ``VG038:1``.
        Clear: ``VG037:-1`` then ``VG038:0``.
        """
        responses: list[dict[str, Any]] = []
        if enabled:
            responses.append(self.publish(commands.cmd_force_cut_duration(minutes)))
            if settle_s > 0:
                time.sleep(settle_s)
            responses.append(self.publish(commands.cmd_force_cut_enabled(True)))
        else:
            responses.append(
                self.publish(commands.cmd_force_cut_duration(commands.FORCE_CUT_CLEAR_MINUTES))
            )
            if settle_s > 0:
                time.sleep(settle_s)
            responses.append(self.publish(commands.cmd_force_cut_enabled(False)))
        return responses

    def set_performance_backup_level(
        self,
        level: int,
        *,
        force: bool = False,
        check_mains: bool = True,
    ) -> dict[str, Any]:
        """Set linked performance/backup level (1..7).

        The official app blocks this while mains is available. When
        ``check_mains`` is true, poll once and refuse unless ``force``.
        """
        if check_mains and not force:
            snap = self.get_dashboard(wake=False, retries=1, wake_wait=0)
            if snap.dashboard.get("is_on_mains"):
                raise VGuardError(
                    "Performance/backup cannot be changed while mains is available "
                    "(same rule as the official app). Force-cut first, wait until "
                    "off mains, then retry — or pass force=True to override."
                )
        return self.publish(commands.cmd_performance_backup_level(level))

    def set_load_alarm_percent(self, percent: int) -> dict[str, Any]:
        return self.publish(commands.cmd_load_alarm_percent(percent))

    def set_mains_changeover_buzzer(self, enabled: bool) -> dict[str, Any]:
        return self.publish(commands.cmd_mains_changeover_buzzer(enabled))

    def set_day_time_load_usage(
        self,
        enabled: bool,
        *,
        force: bool = False,
        check_power_saver_max: bool = True,
    ) -> dict[str, Any]:
        """Set daytime load usage (ON→VG185:0, OFF→VG185:1).

        Official app only allows this when Power Saver is Max (VG183=1):
        snackbar \"It works only in power saver max mode.\"
        """
        if check_power_saver_max and not force:
            snap = self.get_dashboard(wake=False, retries=1, wake_wait=0)
            if snap.dashboard.get("power_saver_mode") != 1:
                raise VGuardError(
                    "Daytime load usage works only in Power Saver Max mode "
                    "(same rule as the official app). Set Power Saver to Max with "
                    "the rear hardware switch, then retry — or pass force=True to override."
                )
        return self.publish(commands.cmd_day_time_load_usage(enabled))

    def set_power_saver_max(self, enabled: bool) -> dict[str, Any]:
        raise VGuardError(
            "Power Saver cannot be changed over the cloud — use the physical "
            "switch on the back of the inverter (same as the official app)."
        )
