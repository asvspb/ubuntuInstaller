#!/usr/bin/env python3
# ==============================================================================
# layout-osd.py — Легковесный нативный OSD-индикатор раскладки для GNOME / X11
#                 Показывает системное всплывающее окно в стиле GNOME Shell
#                 по центру экрана при любой смене языка ввода (Ctrl, Alt, etc.)
# ==============================================================================

import sys
import os
import signal
import ctypes
import cairo

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

class XEvent(ctypes.Structure):
    _fields_ = [("pad", ctypes.c_long * 24)]

class LayoutOSD(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)

        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.box.set_name("osd-container")

        self.lbl_en = Gtk.Label(label="English (US)")
        self.lbl_en.get_style_context().add_class("lang-badge")

        self.lbl_ru = Gtk.Label(label="Русский")
        self.lbl_ru.get_style_context().add_class("lang-badge")

        self.box.pack_start(self.lbl_en, False, False, 0)
        self.box.pack_start(self.lbl_ru, False, False, 0)
        self.add(self.box)

        css_provider = Gtk.CssProvider()
        css = b"""
        #osd-container {
            background-color: rgba(29, 29, 29, 0.95);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 20px;
            padding: 12px 20px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
        }
        .lang-badge {
            font-family: 'Cantarell', 'Ubuntu', 'DejaVu Sans', sans-serif;
            font-size: 17px;
            font-weight: 700;
            color: #777777;
            padding: 8px 18px;
            border-radius: 12px;
            border: 1px solid transparent;
        }
        .lang-badge.active {
            color: #ffffff;
            background-color: rgba(255, 255, 255, 0.18);
            border: 1px solid rgba(255, 255, 255, 0.28);
        }
        """
        css_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.connect("realize", self.on_realize)
        self.connect("draw", self.on_draw)

        self.hide_timer_id = None
        self.last_group = -1

        self.init_x11()

    def on_realize(self, widget):
        # Окно прозрачно для кликов мыши (клики проходят сквозь него)
        self.get_window().input_shape_combine_region(cairo.Region(), 0, 0)

    def on_draw(self, widget, cr):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False

    def init_x11(self):
        self.x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.disp = self.x11.XOpenDisplay(None)
        if not self.disp:
            print("[LayoutOSD] Failed to open X display", file=sys.stderr, flush=True)
            sys.exit(1)

        opcode = ctypes.c_int()
        self.event_base = ctypes.c_int()
        error_base = ctypes.c_int()
        major = ctypes.c_int(1)
        minor = ctypes.c_int(0)

        self.x11.XkbQueryExtension.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int)
        ]
        self.x11.XkbQueryExtension(self.disp, ctypes.byref(opcode), ctypes.byref(self.event_base),
                                  ctypes.byref(error_base), ctypes.byref(major), ctypes.byref(minor))

        XkbUseCoreKbd = 0x0100
        XkbStateNotifyMask = 1 << 2
        self.x11.XkbSelectEvents.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
        self.x11.XkbSelectEvents(self.disp, XkbUseCoreKbd, XkbStateNotifyMask, XkbStateNotifyMask)

        fd = self.x11.XConnectionNumber(self.disp)
        GLib.io_add_watch(fd, GLib.IO_IN, self.on_x11_event)

    def on_x11_event(self, source, condition):
        while self.x11.XPending(self.disp) > 0:
            ev = XEvent()
            self.x11.XNextEvent(self.disp, ctypes.byref(ev))
            ev_ptr = ctypes.cast(ctypes.byref(ev), ctypes.c_void_p).value
            ev_type = ctypes.c_int.from_address(ev_ptr).value
            if ev_type == self.event_base.value:
                # offset 40 is xkb_type
                xkb_type = ctypes.c_int.from_address(ev_ptr + 40).value
                if xkb_type == 2:  # XkbStateNotify
                    # offset 52 is group
                    group = ctypes.c_int.from_address(ev_ptr + 52).value
                    self.show_group(group)
        return True

    def show_group(self, group):
        is_ru = (group == 1)
        if is_ru:
            self.lbl_en.get_style_context().remove_class("active")
            self.lbl_ru.get_style_context().add_class("active")
        else:
            self.lbl_ru.get_style_context().remove_class("active")
            self.lbl_en.get_style_context().add_class("active")

        self.show_all()
        self.check_resize()
        w, h = self.get_size()
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()
        x = geom.x + (geom.width - w) // 2
        y = geom.y + (geom.height - h) // 2
        self.move(x, y)
        self.present()

        if self.hide_timer_id:
            GLib.source_remove(self.hide_timer_id)
        self.hide_timer_id = GLib.timeout_add(800, self.hide_osd)

    def hide_osd(self):
        self.hide()
        self.hide_timer_id = None
        return False

def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    app = LayoutOSD()
    Gtk.main()

if __name__ == "__main__":
    main()
