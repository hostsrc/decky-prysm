#!/usr/bin/env python3
"""Test PipeWire portal connection in-process via ctypes."""

import dbus
import os
import ctypes
import time
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

pw = ctypes.CDLL("libpipewire-0.3.so")
pw.pw_init(None, None)

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
        print(f"[1] Session: {state['session']}")
        sc.SelectSources(
            dbus.ObjectPath(state["session"]),
            dbus.Dictionary({
                "handle_token": dbus.String("sel"),
                "types": dbus.UInt32(1),
            }, signature="sv"),
        )

    elif step == 2:
        print("[2] Sources selected")
        sc.Start(
            dbus.ObjectPath(state["session"]), "",
            dbus.Dictionary({
                "handle_token": dbus.String("go"),
            }, signature="sv"),
        )

    elif step == 3:
        streams = results.get("streams", [])
        if not streams:
            print("[3] No streams!")
            loop.quit()
            return

        nid = int(streams[0][0])
        fd_obj = sc.OpenPipeWireRemote(
            dbus.ObjectPath(state["session"]),
            dbus.Dictionary({}, signature="sv"),
        )
        pw_fd = fd_obj.take()
        print(f"[3] Node={nid} FD={pw_fd}")

        # Connect PipeWire via portal FD (in-process)
        pw.pw_main_loop_new.restype = ctypes.c_void_p
        pw.pw_main_loop_get_loop.restype = ctypes.c_void_p
        pw.pw_context_new.restype = ctypes.c_void_p
        pw.pw_context_connect_fd.restype = ctypes.c_void_p

        ml = pw.pw_main_loop_new(None)
        lp = pw.pw_main_loop_get_loop(ml)
        ctx = pw.pw_context_new(lp, None, 0)

        core = pw.pw_context_connect_fd(ctx, ctypes.c_int(pw_fd), None, 0)
        if core:
            print(f"[4] CONNECTED! core={core}")

            # Now try to iterate the PW loop briefly to process events
            pw.pw_main_loop_quit.argtypes = [ctypes.c_void_p]

            # Set up a timer to quit after 100ms
            # We can't easily set PW timers from ctypes,
            # so just do a non-blocking iteration
            pw.pw_loop_iterate.restype = ctypes.c_int
            pw.pw_loop_iterate.argtypes = [ctypes.c_void_p, ctypes.c_int]

            for i in range(10):
                n = pw.pw_loop_iterate(lp, 0)  # non-blocking
                time.sleep(0.01)

            print("[5] PipeWire loop iterated — connection alive!")

            # Try to create a stream
            pw.pw_stream_new.restype = ctypes.c_void_p
            pw.pw_stream_new.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]

            stream = pw.pw_stream_new(core, b"prysm-test", None)
            if stream:
                print(f"[6] Stream created: {stream}")
            else:
                print("[6] Stream creation FAILED")
        else:
            print("[4] FAILED to connect via FD")

        loop.quit()


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
pw.pw_deinit()
print("Done")
