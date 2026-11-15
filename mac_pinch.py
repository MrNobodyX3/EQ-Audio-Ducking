"""Deliver AppKit's native pinch events to the timeline on macOS."""

from collections import deque
import ctypes as ct


_MAGNIFY_MASK = 1 << 30  # NSEventMaskMagnify
_GLOBAL_BLOCK = 1 << 28
_BLOCK_HAS_SIGNATURE = 1 << 30
_EventHandler = ct.CFUNCTYPE(ct.c_void_p, ct.c_void_p, ct.c_void_p)


class _BlockDescriptor(ct.Structure):
    _fields_ = [
        ("reserved", ct.c_ulong),
        ("size", ct.c_ulong),
        ("signature", ct.c_char_p),
    ]


class _Block(ct.Structure):
    _fields_ = [
        ("isa", ct.c_void_p),
        ("flags", ct.c_int),
        ("reserved", ct.c_int),
        ("invoke", _EventHandler),
        ("descriptor", ct.POINTER(_BlockDescriptor)),
    ]


class MacPinchMonitor:
    """An app-local NSEvent monitor. Install and remove on the Tk main thread."""

    def __init__(self):
        self._deltas = deque(maxlen=256)
        runtime = ct.CDLL("/usr/lib/libobjc.A.dylib")
        system = ct.CDLL("/usr/lib/libSystem.B.dylib")
        runtime.objc_getClass.argtypes = [ct.c_char_p]
        runtime.objc_getClass.restype = ct.c_void_p
        runtime.sel_registerName.argtypes = [ct.c_char_p]
        runtime.sel_registerName.restype = ct.c_void_p
        self._event_class = runtime.objc_getClass(b"NSEvent")
        if not self._event_class:
            raise RuntimeError("AppKit must be running before registering the pinch monitor.")
        self._magnification = runtime.sel_registerName(b"magnification")
        self._remove_selector = runtime.sel_registerName(b"removeMonitor:")

        # Separate typed entry points keep the 64-bit Objective-C calling convention intact.
        get_delta = ct.CFUNCTYPE(ct.c_double, ct.c_void_p, ct.c_void_p)(
            ("objc_msgSend", runtime))
        install = ct.CFUNCTYPE(ct.c_void_p, ct.c_void_p, ct.c_void_p, ct.c_ulong,
                               ct.POINTER(_Block))(("objc_msgSend", runtime))
        self._remove = ct.CFUNCTYPE(None, ct.c_void_p, ct.c_void_p, ct.c_void_p)(
            ("objc_msgSend", runtime))

        def handle(_block, event):
            # AppKit can call this while _tkinter has released the GIL inside
            # Tcl_DoOneEvent. Re-entering Tcl here corrupts its saved thread state.
            if event:
                self._deltas.append(get_delta(event, self._magnification))
            return event

        # An Objective-C block without captured native storage. Python retains the
        # callback and descriptor through the lifetime of the registered monitor.
        self._handler = _EventHandler(handle)
        self._descriptor = _BlockDescriptor(0, ct.sizeof(_Block), b"@16@?0@8")
        global_class = ct.c_void_p.in_dll(system, "_NSConcreteGlobalBlock")
        self._block = _Block(ct.addressof(global_class), _GLOBAL_BLOCK | _BLOCK_HAS_SIGNATURE,
                             0, self._handler, ct.pointer(self._descriptor))
        add_selector = runtime.sel_registerName(b"addLocalMonitorForEventsMatchingMask:handler:")
        self._monitor = install(self._event_class, add_selector, _MAGNIFY_MASK, ct.byref(self._block))
        if not self._monitor:
            raise RuntimeError("AppKit could not register the pinch monitor.")

    def drain(self):
        deltas = tuple(self._deltas)
        self._deltas.clear()
        return deltas

    def close(self):
        if self._monitor:
            self._remove(self._event_class, self._remove_selector, self._monitor)
            self._monitor = None
