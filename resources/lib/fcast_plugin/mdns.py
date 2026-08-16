"""mDNS/DNS-SD advertisement of the FCast receiver via Avahi.

Kodi runs on platforms that ship mutually exclusive D-Bus bindings: Debian and
Raspbian have python-dbus, LibreELEC and CoreELEC have DBussy. Practically no
platform has both, so each backend is imported inside its own probe and they
are tried in turn. Nothing here may raise at import time -- a top-level
``import`` of a binding that happens to be missing took the whole service down
on Raspbian once already.
"""

import socket
import subprocess
import time
from typing import Dict, List, Optional

import xbmc

from .util import log, addonname, addonversion

SERVICE_TYPE = "_fcast._tcp"
SERVICE_PORT = 46899

AVAHI_BUS_NAME = "org.freedesktop.Avahi"
AVAHI_SERVER_IFACE = "org.freedesktop.Avahi.Server"
AVAHI_ENTRY_GROUP_IFACE = "org.freedesktop.Avahi.EntryGroup"

# Avahi's "daemon decides" sentinels: AVAHI_IF_UNSPEC / AVAHI_PROTO_UNSPEC.
IF_UNSPEC = -1
PROTO_UNSPEC = -1

DBUS_TIMEOUT_MS = 5000

_backend = None


def _service_name() -> str:
    return f"Kodi - {socket.gethostname()}"


def _txt_records(protocol_version: int) -> List[bytes]:
    # Some sender mDNS stacks misbehave against a service with no TXT records
    # at all, so always publish these three. Same fields the reference FCast
    # receiver advertises.
    records: Dict[str, str] = {
        "version": str(protocol_version),
        "appName": addonname,
        "appVersion": addonversion,
    }
    return [f"{key}={value}".encode("utf-8") for key, value in records.items()]


class _DbusBackend:
    """python-dbus, as found on Debian, Raspbian, Ubuntu and Arch."""

    name = "python-dbus"

    def __init__(self) -> None:
        self._group = None

    def register(self, service_name: str, port: int, txt: List[bytes]) -> None:
        import dbus

        bus = dbus.SystemBus()
        server = dbus.Interface(
            bus.get_object(AVAHI_BUS_NAME, "/"),
            AVAHI_SERVER_IFACE,
        )
        self._group = dbus.Interface(
            bus.get_object(AVAHI_BUS_NAME, server.EntryGroupNew()),
            AVAHI_ENTRY_GROUP_IFACE,
        )
        self._group.AddService(
            IF_UNSPEC,
            PROTO_UNSPEC,
            dbus.UInt32(0),
            service_name,
            SERVICE_TYPE,
            "",  # domain: default (.local)
            "",  # host: default (this machine)
            dbus.UInt16(port),
            dbus.Array([dbus.ByteArray(record) for record in txt], signature="ay"),
        )
        self._group.Commit()

    def unregister(self) -> None:
        if self._group is not None:
            self._group.Reset()
            self._group = None


class _DbussyBackend:
    """DBussy, the pure-python ctypes binding shipped by LibreELEC and CoreELEC."""

    name = "dbussy"

    def __init__(self) -> None:
        self._conn = None
        self._group_path = None

    def _call(self, path: str, iface: str, method: str):
        import dbussy

        message = dbussy.Message.new_method_call(
            destination=AVAHI_BUS_NAME, path=path, iface=iface, method=method
        )
        return message

    def register(self, service_name: str, port: int, txt: List[bytes]) -> None:
        import dbussy
        from dbussy import DBUS

        self._conn = dbussy.Connection.bus_get(DBUS.BUS_SYSTEM, private=False)

        reply = self._conn.send_with_reply_and_block(
            self._call("/", AVAHI_SERVER_IFACE, "EntryGroupNew"),
            timeout=DBUS_TIMEOUT_MS,
        )
        self._group_path = reply.expect_return_objects("o")[0]

        add = self._call(self._group_path, AVAHI_ENTRY_GROUP_IFACE, "AddService")
        add.append_objects(
            "iiussssq",
            IF_UNSPEC,
            PROTO_UNSPEC,
            0,
            service_name,
            SERVICE_TYPE,
            "",  # domain: default (.local)
            "",  # host: default (this machine)
            port,
        )
        # DBussy's append_objects() crashes on a nested "aay" (it assumes the
        # array element type is a basic type), so the TXT array is opened by
        # hand. A second iter_init_append() keeps appending where the first
        # one left off.
        txt_array = add.iter_init_append().open_container(DBUS.TYPE_ARRAY, "ay")
        for record in txt:
            entry = txt_array.open_container(DBUS.TYPE_ARRAY, "y")
            entry.append_fixed_array(DBUS.TYPE_BYTE, list(record))
            entry.close()
        txt_array.close()
        self._conn.send_with_reply_and_block(add, timeout=DBUS_TIMEOUT_MS)

        self._conn.send_with_reply_and_block(
            self._call(self._group_path, AVAHI_ENTRY_GROUP_IFACE, "Commit"),
            timeout=DBUS_TIMEOUT_MS,
        )

    def unregister(self) -> None:
        if self._conn is not None and self._group_path is not None:
            self._conn.send_with_reply_and_block(
                self._call(self._group_path, AVAHI_ENTRY_GROUP_IFACE, "Reset"),
                timeout=DBUS_TIMEOUT_MS,
            )
        self._group_path = None
        self._conn = None


class _AvahiPublishBackend:
    """avahi-publish-service(1), for systems with Avahi but no usable binding."""

    name = "avahi-publish"

    def __init__(self) -> None:
        self._process = None

    def register(self, service_name: str, port: int, txt: List[bytes]) -> None:
        argv = ["avahi-publish-service", service_name, SERVICE_TYPE, str(port)]
        argv += [record.decode("utf-8") for record in txt]

        self._process = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        # The registration only lives as long as the process does, so an early
        # exit means it failed. Give it a moment, then make sure it is alive.
        time.sleep(0.5)
        if self._process.poll() is not None:
            stderr = self._process.stderr.read().decode("utf-8", "replace").strip()
            self._process = None
            raise RuntimeError(stderr or "avahi-publish-service exited immediately")

    def unregister(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None


# Ordered by preference: a D-Bus binding talks to Avahi directly, the CLI
# helper is the fallback for platforms that ship neither binding.
_BACKENDS = (_DbusBackend, _DbussyBackend, _AvahiPublishBackend)


def register(port: int = SERVICE_PORT, protocol_version: int = 2) -> bool:
    """Advertise the receiver over mDNS. Returns True if a backend succeeded.

    Discovery is a convenience -- senders can still connect by IP -- so failure
    is logged and swallowed rather than propagated.
    """
    global _backend

    if _backend is not None:
        return True

    service_name = _service_name()
    txt = _txt_records(protocol_version)
    failures = []

    for backend_class in _BACKENDS:
        backend = backend_class()
        try:
            backend.register(service_name, port, txt)
        except Exception as e:
            failures.append(f"{backend_class.name}: {e}")
            continue

        _backend = backend
        # Above debug level: "are senders able to find this box" is the first
        # question every discovery problem starts with, and the answer should
        # not need Kodi's debug logging turned on to read.
        log(
            f"mDNS: registered '{service_name}' as {SERVICE_TYPE}:{port} "
            f"via {backend.name}",
            xbmc.LOGINFO,
        )
        return True

    # Not fatal - senders can still be pointed at this box by IP - but it is
    # the difference between the add-on appearing in a sender's device list
    # and not, so it says so where it can be seen.
    log(f"mDNS: no backend could register the service ({'; '.join(failures)})",
        xbmc.LOGWARNING)
    return False


def unregister() -> None:
    global _backend

    if _backend is None:
        return

    try:
        _backend.unregister()
        log(f"mDNS: unregistered service ({_backend.name})")
    except Exception as e:
        log(f"mDNS: failed to unregister via {_backend.name}: {e}")
    finally:
        _backend = None
