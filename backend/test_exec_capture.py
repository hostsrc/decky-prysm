#!/usr/bin/env python3
"""
Get portal FD, then os.execv into pw_capture (same process, FD survives).
Output goes to /tmp/prysm/raw_video.bin, stderr visible.
"""

import dbus
import os
import sys
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

DBusGMainLoop(set_as_default=True)
loop = GLib.MainLoop()
bus = dbus.SessionBus()
portal = bus.get_object("org.freedesktop.portal.Desktop",
                        "/org/freedesktop/portal/desktop")
sc = dbus.Interface(portal, "org.freedesktop.portal.ScreenCast")
state = {"session": None, "step": 0}


def on_response(response, results):
    state["step"] += 1
    step = state["step"]

    if step == 1:
        state["session"] = str(results.get("session_handle", ""))
        print(f"Session: {state['session']}", flush=True)
        sc.SelectSources(
            dbus.ObjectPath(state["session"]),
            dbus.Dictionary({
                "handle_token": dbus.String("sel"),
                "types": dbus.UInt32(1),
            }, signature="sv"),
        )

    elif step == 2:
        print("Sources selected", flush=True)
        sc.Start(
            dbus.ObjectPath(state["session"]), "",
            dbus.Dictionary({"handle_token": dbus.String("go")}, signature="sv"),
        )

    elif step == 3:
        streams = results.get("streams", [])
        if not streams:
            print("No streams!")
            loop.quit()
            return

        nid = int(streams[0][0])
        fd_obj = sc.OpenPipeWireRemote(
            dbus.ObjectPath(state["session"]),
            dbus.Dictionary({}, signature="sv"),
        )
        pw_fd = fd_obj.take()
        print(f"Node={nid} FD={pw_fd}", flush=True)

        # IMPORTANT: Clear the close-on-exec flag so FD survives execv
        import fcntl
        flags = fcntl.fcntl(pw_fd, fcntl.F_GETFD)
        fcntl.fcntl(pw_fd, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)
        print(f"FD {pw_fd} close-on-exec cleared", flush=True)

        # Redirect stdout to file (pw_capture writes raw video to stdout)
        out_fd = os.open("/tmp/prysm/raw_video.bin",
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.dup2(out_fd, 1)  # stdout → file
        os.close(out_fd)

        # Replace this process with pw_capture
        print(f"Exec: /tmp/pw_capture {pw_fd} {nid}", file=sys.stderr, flush=True)
        os.execv("/tmp/pw_capture", ["/tmp/pw_capture", str(pw_fd), str(nid)])


bus.add_signal_receiver(
    on_response,
    signal_name="Response",
    dbus_interface="org.freedesktop.portal.Request",
)

sc.CreateSession(dbus.Dictionary({
    "handle_token": dbus.String("cs"),
    "session_handle_token": dbus.String("ss"),
}, signature="sv"))

GLib.timeout_add_seconds(10, loop.quit)
loop.run()
