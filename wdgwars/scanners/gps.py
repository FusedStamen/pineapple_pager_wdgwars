"""GPS reader via gpsd socket — drop-in replacement for the raw-serial version.

Connects to the local gpsd daemon (localhost:2947) and subscribes to the JSON
watch stream.  The public API (GpsReader, GpsState, GpsSnapshot) is identical
to the original so no other files need to change.

gpsd handles device detection and initialisation for both the u-blox UG-353
and the Quectel LC86 (Glyph mod) — whichever is plugged in will be used
automatically without any config change.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

# ── public data classes ────────────────────────────────────────────────────

@dataclass
class GpsState:
    fix_3d: bool = False
    fix_quality: int = 0
    lat: float = 0.0
    lon: float = 0.0
    alt_m: float = 0.0
    accuracy_m: float = 0.0
    sats: int = 0
    utc_iso: str = ""
    last_update: float = 0.0
    device: str = ""

    lock: threading.Lock = field(default_factory=threading.Lock,
                                  repr=False, compare=False)

    def snapshot(self) -> "GpsSnapshot":
        with self.lock:
            return GpsSnapshot(
                fix_3d=self.fix_3d,
                fix_quality=self.fix_quality,
                lat=self.lat,
                lon=self.lon,
                alt_m=self.alt_m,
                accuracy_m=self.accuracy_m,
                sats=self.sats,
                utc_iso=self.utc_iso,
                last_update=self.last_update,
                device=self.device,
            )


@dataclass(frozen=True)
class GpsSnapshot:
    fix_3d: bool
    fix_quality: int
    lat: float
    lon: float
    alt_m: float
    accuracy_m: float
    sats: int
    utc_iso: str
    last_update: float
    device: str


# ── reader ─────────────────────────────────────────────────────────────────

class GpsReader:
    """Reads GPS fixes from gpsd via its JSON socket on localhost:2947.

    The *devices* and *baud* arguments are accepted for API compatibility but
    are ignored — gpsd owns device selection and baud negotiation.
    """

    GPSD_HOST = "127.0.0.1"
    GPSD_PORT = 2947

    # gpsd watch command — enable JSON, ask for device reports
    _WATCH = b'?WATCH={"enable":true,"json":true}\n'

    def __init__(self, devices: Iterable[str], baud: int = 9600,
                 min_sats: int = 4) -> None:
        # kept for API compatibility; not used
        self.devices = list(devices)
        self.baud = baud
        self.min_sats = min_sats

        self.state = GpsState()
        self._stop = threading.Event()
        self._thr: threading.Thread | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop.clear()
        self._thr = threading.Thread(target=self._run, name="gps", daemon=True)
        self._thr.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thr:
            self._thr.join(timeout=3)
            self._thr = None

    # ── background thread ─────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            sock = self._connect()
            if sock is None:
                # gpsd not ready yet — retry after a short pause
                self._stop.wait(2.0)
                continue
            try:
                self._read_loop(sock)
            except Exception:
                pass
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
            # brief pause before reconnecting
            self._stop.wait(1.0)

    def _connect(self) -> socket.socket | None:
        try:
            s = socket.create_connection(
                (self.GPSD_HOST, self.GPSD_PORT), timeout=5
            )
            s.settimeout(2.0)
            # consume the gpsd banner
            s.recv(4096)
            # enable JSON watch stream
            s.sendall(self._WATCH)
            return s
        except OSError:
            return None

    def _read_loop(self, sock: socket.socket) -> None:
        buf = ""
        while not self._stop.is_set():
            try:
                chunk = sock.recv(4096).decode("utf-8", errors="ignore")
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while "\n" in buf:
                line, _, buf = buf.partition("\n")
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._apply(obj)

    # ── state update ──────────────────────────────────────────────────────

    def _apply(self, obj: dict) -> None:
        cls = obj.get("class", "")

        if cls == "DEVICES":
            # pick up the first active device path for display
            devices = obj.get("devices", [])
            if devices:
                path = devices[0].get("path", "")
                if path:
                    with self.state.lock:
                        self.state.device = path

        elif cls == "DEVICE":
            path = obj.get("path", "")
            if path:
                with self.state.lock:
                    self.state.device = path

        elif cls == "TPV":
            # TPV = time-position-velocity report
            mode = obj.get("mode", 0)
            # mode 3 = 3D fix, mode 2 = 2D fix, mode 1 = no fix
            lat = obj.get("lat")
            lon = obj.get("lon")
            alt = obj.get("alt", obj.get("altMSL", 0.0))
            eph = obj.get("eph", 0.0)   # horizontal position error (metres)
            time_str = obj.get("time", "")
            path = obj.get("device", "")

            with self.state.lock:
                if path:
                    self.state.device = path
                self.state.last_update = time.time()

                if mode >= 2 and lat is not None and lon is not None:
                    self.state.lat = float(lat)
                    self.state.lon = float(lon)
                    self.state.alt_m = float(alt) if alt is not None else 0.0
                    self.state.accuracy_m = float(eph) if eph else 0.0
                    self.state.fix_quality = mode
                    if time_str:
                        self.state.utc_iso = time_str
                    # require min_sats for a "good" 3D fix
                    self.state.fix_3d = (
                        mode == 3 and self.state.sats >= self.min_sats
                    )
                else:
                    self.state.fix_quality = 0
                    self.state.fix_3d = False

        elif cls == "SKY":
            # SKY = satellite constellation report
            used = [s for s in obj.get("satellites", []) if s.get("used")]
            n_used = len(used) if used else obj.get("uSat", 0)
            with self.state.lock:
                self.state.sats = int(n_used)
                # re-evaluate fix_3d with updated sat count
                if self.state.fix_quality == 3:
                    self.state.fix_3d = self.state.sats >= self.min_sats
