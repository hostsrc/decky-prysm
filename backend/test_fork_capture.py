#!/usr/bin/env python3
"""Fork-based capture: parent keeps portal alive, child runs pw_capture."""

import dbus
import os
import sys
import time
import fcntl
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

        # Clear close-on-exec
        flags = fcntl.fcntl(pw_fd, fcntl.F_GETFD)
        fcntl.fcntl(pw_fd, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)

        # Fork: child runs pw_capture, parent keeps portal alive
        pid = os.fork()
        if pid == 0:
            # Child: redirect stdout to file, exec pw_capture
            out_fd = os.open("/tmp/prysm/raw_video.bin",
                             os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            os.dup2(out_fd, 1)
            os.close(out_fd)
            os.execv("/tmp/pw_capture", ["/tmp/pw_capture", str(pw_fd), str(nid)])
        else:
            # Parent: keep running GLib loop to maintain portal session
            print(f"Child PID={pid}, parent keeping portal alive...", flush=True)

            def check_child():
                try:
                    p, status = os.waitpid(pid, os.WNOHANG)
                    if p != 0:
                        print(f"Child exited: {status}", flush=True)
                        loop.quit()
                        return False
                except:
                    pass
                return True

            GLib.timeout_add(500, check_child)
            # Auto-kill after 10 seconds
            GLib.timeout_add_seconds(10, lambda: (os.kill(pid, 15), loop.quit(), False)[-1])


bus.add_signal_receiver(
    on_response,
    signal_name="Response",
    dbus_interface="org.freedesktop.portal.Request",
)

sc.CreateSession(dbus.Dictionary({
    "handle_token": dbus.String("cs"),
    "session_handle_token": dbus.String("ss"),
}, signature="sv"))

GLib.timeout_add_seconds(20, loop.quit)
loop.run()

# Check result
try:
    sz = os.path.getsize("/tmp/prysm/raw_video.bin")
    print(f"\nResult: {sz} bytes captured!")
except:
    print("\nNo output file")
