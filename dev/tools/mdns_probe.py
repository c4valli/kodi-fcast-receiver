#!/usr/bin/env python3
"""Report which mDNS backend works on this machine.

Runs standalone, outside Kodi -- copy it to the device and run it with the
same interpreter Kodi uses. It performs a real Avahi registration, holds it
for a few seconds so you can see the service with `avahi-browse -rt _fcast._tcp`
from another box, then cleans up.

    python3 mdns_probe.py
"""

import os
import socket
import subprocess
import sys
import time

SERVICE_TYPE = "_fcast._tcp"
SERVICE_PORT = 46899
HOLD_SECONDS = 10

AVAHI_BUS_NAME = "org.freedesktop.Avahi"
AVAHI_SERVER_IFACE = "org.freedesktop.Avahi.Server"
AVAHI_ENTRY_GROUP_IFACE = "org.freedesktop.Avahi.EntryGroup"
IF_UNSPEC = PROTO_UNSPEC = -1
DBUS_TIMEOUT_MS = 5000

NAME = "FCast probe - %s" % socket.gethostname()
TXT = [b"version=2", b"appName=FCast Receiver", b"appVersion=probe"]


def report(label, ok, detail=""):
    print("  %-16s %-9s %s" % (label, "OK" if ok else "FAILED", detail))


def probe_environment():
    print("Environment")
    print("  python           %s" % sys.version.split()[0])
    print("  executable       %s" % sys.executable)
    print("  platform         %s" % sys.platform)
    if os.path.exists("/etc/os-release"):
        with open("/etc/os-release") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    print("  os               %s" % line.split("=", 1)[1].strip().strip('"'))
    socket_path = "/run/dbus/system_bus_socket"
    print("  %-16s %s" % ("system bus", "present" if os.path.exists(socket_path) else "MISSING at " + socket_path))
    print()

    print("Bindings")
    for module in ("dbus", "dbussy", "ravel"):
        try:
            imported = __import__(module)
            report(module, True, getattr(imported, "__file__", ""))
        except Exception as e:
            report(module, False, str(e))
    for binary in ("avahi-publish-service", "avahi-browse", "avahi-daemon"):
        try:
            path = subprocess.check_output(["which", binary], stderr=subprocess.DEVNULL)
            report(binary, True, path.decode().strip())
        except Exception:
            report(binary, False, "not on PATH")
    print()


def probe_dbus():
    import dbus

    bus = dbus.SystemBus()
    server = dbus.Interface(bus.get_object(AVAHI_BUS_NAME, "/"), AVAHI_SERVER_IFACE)
    group = dbus.Interface(
        bus.get_object(AVAHI_BUS_NAME, server.EntryGroupNew()), AVAHI_ENTRY_GROUP_IFACE
    )
    group.AddService(
        IF_UNSPEC, PROTO_UNSPEC, dbus.UInt32(0), NAME, SERVICE_TYPE, "", "",
        dbus.UInt16(SERVICE_PORT),
        dbus.Array([dbus.ByteArray(r) for r in TXT], signature="ay"),
    )
    group.Commit()
    return lambda: group.Reset()


def probe_dbussy():
    import dbussy
    from dbussy import DBUS

    conn = dbussy.Connection.bus_get(DBUS.BUS_SYSTEM, private=False)

    def call(path, iface, method):
        return dbussy.Message.new_method_call(
            destination=AVAHI_BUS_NAME, path=path, iface=iface, method=method
        )

    reply = conn.send_with_reply_and_block(
        call("/", AVAHI_SERVER_IFACE, "EntryGroupNew"), timeout=DBUS_TIMEOUT_MS
    )
    group_path = reply.expect_return_objects("o")[0]

    add = call(group_path, AVAHI_ENTRY_GROUP_IFACE, "AddService")
    add.append_objects(
        "iiussssq", IF_UNSPEC, PROTO_UNSPEC, 0, NAME, SERVICE_TYPE, "", "", SERVICE_PORT
    )
    txt_array = add.iter_init_append().open_container(DBUS.TYPE_ARRAY, "ay")
    for record in TXT:
        entry = txt_array.open_container(DBUS.TYPE_ARRAY, "y")
        entry.append_fixed_array(DBUS.TYPE_BYTE, list(record))
        entry.close()
    txt_array.close()
    conn.send_with_reply_and_block(add, timeout=DBUS_TIMEOUT_MS)
    conn.send_with_reply_and_block(
        call(group_path, AVAHI_ENTRY_GROUP_IFACE, "Commit"), timeout=DBUS_TIMEOUT_MS
    )

    return lambda: conn.send_with_reply_and_block(
        call(group_path, AVAHI_ENTRY_GROUP_IFACE, "Reset"), timeout=DBUS_TIMEOUT_MS
    )


def probe_avahi_publish():
    argv = ["avahi-publish-service", NAME, SERVICE_TYPE, str(SERVICE_PORT)]
    argv += [r.decode() for r in TXT]
    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    time.sleep(0.5)
    if process.poll() is not None:
        raise RuntimeError(process.stderr.read().decode("utf-8", "replace").strip())
    return lambda: process.terminate()


def main():
    probe_environment()

    print("Registration (each backend registers for real, then resets)")
    working = []
    for label, probe in (
        ("python-dbus", probe_dbus),
        ("dbussy", probe_dbussy),
        ("avahi-publish", probe_avahi_publish),
    ):
        try:
            cleanup = probe()
        except Exception as e:
            report(label, False, "%s: %s" % (type(e).__name__, e))
            continue

        report(label, True, "registered")
        working.append(label)
        print("      holding %ds -- check with: avahi-browse -rt %s" % (HOLD_SECONDS, SERVICE_TYPE))
        time.sleep(HOLD_SECONDS)
        try:
            cleanup()
        except Exception as e:
            print("      cleanup failed: %s" % e)

    print()
    if working:
        print("Result: the add-on will use %s" % working[0])
    else:
        print("Result: NO backend works -- the add-on will start but stay undiscoverable.")
        print("        Senders can still connect by IP on port %d." % SERVICE_PORT)
    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())
