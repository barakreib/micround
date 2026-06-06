#!/usr/bin/env python3
"""
Micround Client v2 — Displays a microscope stream as an animated desktop wallpaper.

Run this on the Mac where you want the wallpaper to appear.
Usage:
    python3 client.py                              # auto-discover on LAN
    python3 client.py ws://192.168.1.5:9876        # connect to a specific server
    python3 client.py wss://xxx.ngrok-free.app     # connect via ngrok
"""

import asyncio
import logging
import queue
import socket
import sys
import threading

import objc
import websockets
from Cocoa import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApp,
    NSBackingStoreBuffered,
    NSColor,
    NSImage,
    NSImageView,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSScreen,
    NSStatusBar,
    NSTextField,
    NSVariableStatusItemLength,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSData, NSTimer
from zeroconf import ServiceBrowser, Zeroconf

# ── Configuration ─────────────────────────────────────────────────────────────

SERVICE_TYPE = "_micround._tcp.local."
RECONNECT_MAX_DELAY = 30        # Maximum seconds between reconnect attempts
FRAME_POLL_HZ = 60              # How often (Hz) the main thread polls for new frames
WS_MAX_SIZE = 10_000_000        # 10 MB max WebSocket message

# NSImageView scaling:  3 = NSImageScaleProportionallyUpOrDown
IMAGE_SCALE_FIT = 3

# Desktop-level window constant (sits behind all other windows)
kCGDesktopWindowLevel = -2147483603

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("micround-client")


# ── Background Window ─────────────────────────────────────────────────────────

class BackgroundWindow(NSWindow):
    """An NSWindow subclass that refuses to become key or main,
    ensuring clicks always pass through to the real desktop."""

    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


# ── Bonjour Discovery ─────────────────────────────────────────────────────────

class MicroundDiscovery:
    """Discovers Micround servers on the local network via Bonjour/mDNS."""

    def __init__(self, on_found, on_lost):
        self._on_found = on_found
        self._on_lost = on_lost
        self._zeroconf = None
        self._browser = None

    def start(self):
        self._zeroconf = Zeroconf()
        self._browser = ServiceBrowser(self._zeroconf, SERVICE_TYPE, self)
        log.info("Bonjour discovery started — scanning LAN…")

    def stop(self):
        if self._zeroconf:
            self._zeroconf.close()
            self._zeroconf = None
            self._browser = None
            log.info("Bonjour discovery stopped")

    # ── ServiceBrowser listener callbacks ─────────────────────────────────

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info:
            addresses = info.parsed_addresses()
            if addresses:
                url = f"ws://{addresses[0]}:{info.port}"
                server_name = (
                    info.properties.get(b"name", b"").decode("utf-8", errors="replace")
                    or info.server
                    or name
                )
                log.info(f"Server found via Bonjour: {server_name} → {url}")
                self._on_found(url, server_name)

    def remove_service(self, zc, type_, name):
        log.info(f"Server lost: {name}")
        self._on_lost(name)

    def update_service(self, zc, type_, name):
        pass


# ── Application Delegate ──────────────────────────────────────────────────────

class AppDelegate(NSObject):
    """
    Drives the entire client application:
      • Desktop-level window with NSImageView for wallpaper rendering
      • Menu bar status item for user controls
      • Background asyncio thread for WebSocket + Bonjour
      • Thread-safe queue for passing frames to the main thread
    """

    def init(self):
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None

        # Connection state
        self._server_url = None
        self._server_name = None
        self._connected = False
        self._connecting = False
        self._initial_url = None       # Set before app.run() if provided via CLI

        # Thread communication
        self._frame_queue = queue.Queue(maxsize=3)
        self._status_queue = queue.Queue()

        # Async event loop (runs in background thread)
        self._async_loop = None
        self._async_thread = None
        self._loop_ready = threading.Event()
        self._connect_task = None

        # Bonjour
        self._discovery = None

        # UI handles (set up in applicationDidFinishLaunching_)
        self._window = None
        self._image_view = None
        self._status_item = None
        self._status_menu_item = None
        self._server_url_item = None
        self._connect_menu_item = None

        return self

    # ── NSApplicationDelegate ─────────────────────────────────────────────

    def applicationDidFinishLaunching_(self, notification):
        self._setup_desktop_window()
        self._setup_menu_bar()
        self._start_async_thread()
        self._start_frame_timer()
        self._start_discovery()

        # If a URL was provided on the command line, connect to it
        if self._initial_url:
            self._connect_to(self._initial_url, "CLI")

    # ── Desktop Window Setup ──────────────────────────────────────────────

    def _setup_desktop_window(self):
        """Create a borderless, fullscreen window at the desktop level."""
        rect = NSScreen.mainScreen().frame()

        self._window = (
            BackgroundWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                rect,
                NSWindowStyleMaskBorderless,
                NSBackingStoreBuffered,
                False,
            )
        )
        self._window.setLevel_(kCGDesktopWindowLevel)
        self._window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        self._window.setBackgroundColor_(NSColor.blackColor())
        self._window.setOpaque_(True)

        # Image view fills the entire window
        self._image_view = NSImageView.alloc().initWithFrame_(rect)
        self._image_view.setImageScaling_(IMAGE_SCALE_FIT)

        self._window.setContentView_(self._image_view)
        self._window.orderFrontRegardless()
        log.info("Desktop wallpaper window created")

    # ── Menu Bar Setup ────────────────────────────────────────────────────

    def _setup_menu_bar(self):
        """Build the status-bar dropdown menu."""
        status_bar = NSStatusBar.systemStatusBar()
        self._status_item = status_bar.statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._status_item.setTitle_("🔬")

        menu = NSMenu.alloc().init()

        # ── Status (disabled / informational) ─────────────────────────────
        self._status_menu_item = menu.addItemWithTitle_action_keyEquivalent_(
            "Status: Searching…", None, ""
        )
        self._status_menu_item.setEnabled_(False)

        self._server_url_item = menu.addItemWithTitle_action_keyEquivalent_(
            "Server: —", None, ""
        )
        self._server_url_item.setEnabled_(False)

        menu.addItem_(NSMenuItem.separatorItem())

        # ── Discovery / manual connect ────────────────────────────────────
        item = menu.addItemWithTitle_action_keyEquivalent_(
            "🔍 Scan for servers (LAN)", "scanForServers:", ""
        )
        item.setTarget_(self)

        item = menu.addItemWithTitle_action_keyEquivalent_(
            "🔗 Enter URL manually…", "enterUrlManually:", ""
        )
        item.setTarget_(self)

        menu.addItem_(NSMenuItem.separatorItem())

        # ── Connect / Disconnect ──────────────────────────────────────────
        self._connect_menu_item = menu.addItemWithTitle_action_keyEquivalent_(
            "Disconnect", "toggleConnection:", ""
        )
        self._connect_menu_item.setTarget_(self)
        self._connect_menu_item.setEnabled_(False)

        menu.addItem_(NSMenuItem.separatorItem())

        # ── Quit ──────────────────────────────────────────────────────────
        item = menu.addItemWithTitle_action_keyEquivalent_(
            "Quit", "quitApp:", "q"
        )
        item.setTarget_(self)

        self._status_item.setMenu_(menu)

    # ── Menu Actions ──────────────────────────────────────────────────────

    @objc.python_method
    def _set_status(self, text):
        """Update the status line and menu bar icon to reflect the current state."""
        self._status_menu_item.setTitle_(f"Status: {text}")
        if "Connected" == text:
            self._status_item.setTitle_("📡")
        elif "Reconnecting" in text or "Connecting" in text:
            self._status_item.setTitle_("🟡")
        else:
            self._status_item.setTitle_("🔬")

    def scanForServers_(self, sender):
        """Re-start Bonjour discovery."""
        self._stop_discovery()
        self._start_discovery()
        self._set_status("Searching…")

    def enterUrlManually_(self, sender):
        """Show a dialog where the user can paste a WebSocket URL."""
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Connect to Micround Server")
        alert.setInformativeText_(
            "Enter the WebSocket URL of the server.\n\n"
            "Examples:\n"
            "  ws://192.168.1.5:9876          (LAN)\n"
            "  wss://abc123.ngrok-free.app    (remote)"
        )
        alert.addButtonWithTitle_("Connect")
        alert.addButtonWithTitle_("Cancel")

        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 350, 24))
        field.setPlaceholderString_("ws://192.168.1.5:9876")
        alert.setAccessoryView_(field)

        # Make sure the text field gets focus
        alert.window().setInitialFirstResponder_(field)

        response = alert.runModal()
        if response == NSAlertFirstButtonReturn:
            url = str(field.stringValue()).strip()
            if url:
                if not url.startswith("ws://") and not url.startswith("wss://"):
                    url = "ws://" + url
                self._connect_to(url, "Manual")

    def toggleConnection_(self, sender):
        """Toggle between connected and disconnected."""
        if self._connected or self._connecting:
            self._disconnect()
        else:
            if self._server_url:
                self._connect_to(self._server_url, self._server_name or "Server")

    def quitApp_(self, sender):
        """Cleanly shut down everything and quit."""
        self._disconnect()
        self._stop_discovery()
        NSApp.terminate_(self)

    # ── Connection Management ─────────────────────────────────────────────

    @objc.python_method
    def _connect_to(self, url, name="Server"):
        """Initiate a WebSocket connection to the given URL."""
        # Disconnect from any current server first
        if self._connected or self._connecting:
            self._disconnect()

        self._server_url = url
        self._server_name = name
        self._connecting = True

        self._server_url_item.setTitle_(f"Server: {url}")
        self._connect_menu_item.setTitle_("Disconnect")
        self._connect_menu_item.setEnabled_(True)
        self._set_status("Connecting…")

        # Schedule the coroutine on the background async loop
        self._loop_ready.wait()  # Ensure loop is running
        self._connect_task = asyncio.run_coroutine_threadsafe(
            self._ws_connect_loop(url), self._async_loop
        )

    @objc.python_method
    def _disconnect(self):
        """Cancel any active connection and reset state."""
        self._connecting = False
        self._connected = False

        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
            self._connect_task = None

        self._connect_menu_item.setTitle_("Reconnect")
        self._connect_menu_item.setEnabled_(bool(self._server_url))
        self._set_status("Disconnected")

    # ── Async Thread ──────────────────────────────────────────────────────

    @objc.python_method
    def _start_async_thread(self):
        """Spin up a background thread running an asyncio event loop."""

        def _run():
            self._async_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._async_loop)
            self._loop_ready.set()
            self._async_loop.run_forever()

        self._async_thread = threading.Thread(target=_run, daemon=True)
        self._async_thread.start()
        self._loop_ready.wait()
        log.info("Async event loop started")

    async def _ws_connect_loop(self, url):
        """
        Connect to the WebSocket server and receive frames.
        Automatically reconnects with exponential backoff on failure.
        """
        delay = 1

        while self._connecting:
            try:
                log.info(f"Connecting to {url}…")
                async with websockets.connect(url, max_size=WS_MAX_SIZE) as ws:
                    self._connected = True
                    delay = 1  # Reset backoff on successful connection
                    self._status_queue.put(("connected", url))
                    log.info(f"Connected to {url}")

                    async for message in ws:
                        if not self._connecting:
                            break
                        if isinstance(message, bytes):
                            # Put the latest frame in the queue, dropping old ones
                            self._enqueue_frame(message)

            except asyncio.CancelledError:
                log.info("Connection cancelled")
                break
            except Exception as e:
                log.warning(f"Connection error: {e}")

            # If we get here, we disconnected
            self._connected = False
            if self._connecting:
                self._status_queue.put(("reconnecting", delay))
                log.info(f"Reconnecting in {delay}s…")
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break
                delay = min(delay * 2, RECONNECT_MAX_DELAY)

        self._status_queue.put(("disconnected", None))

    @objc.python_method
    def _enqueue_frame(self, jpeg_bytes):
        """Put a frame in the queue, discarding stale frames to avoid lag."""
        # Drain old frames
        while not self._frame_queue.empty():
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                break
        try:
            self._frame_queue.put_nowait(jpeg_bytes)
        except queue.Full:
            pass  # Drop the frame

    # ── Main-Thread Frame Timer ───────────────────────────────────────────

    @objc.python_method
    def _start_frame_timer(self):
        """Start an NSTimer on the main thread to poll for frames and status."""
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / FRAME_POLL_HZ,
            self,
            "pollUpdates:",
            None,
            True,
        )

    def pollUpdates_(self, timer):
        """Called on the main thread ~60 times per second."""
        # ── Render the latest frame ───────────────────────────────────────
        try:
            jpeg_bytes = self._frame_queue.get_nowait()
            ns_data = NSData.dataWithBytes_length_(jpeg_bytes, len(jpeg_bytes))
            image = NSImage.alloc().initWithData_(ns_data)
            if image:
                self._image_view.setImage_(image)
        except queue.Empty:
            pass

        # ── Process status updates from the async thread ──────────────────
        try:
            while True:
                kind, value = self._status_queue.get_nowait()
                if kind == "connected":
                    self._set_status("Connected")
                    self._connect_menu_item.setTitle_("Disconnect")
                    self._connect_menu_item.setEnabled_(True)
                elif kind == "reconnecting":
                    self._set_status(f"Reconnecting ({value}s)…")
                elif kind == "disconnected":
                    if not self._connecting:
                        self._set_status("Disconnected")
                elif kind == "server_found":
                    # Auto-connect to discovered server
                    self._connect_to(value, "LAN Server")
        except queue.Empty:
            pass

    # ── Bonjour Discovery ─────────────────────────────────────────────────

    @objc.python_method
    def _start_discovery(self):
        """Begin scanning the LAN for Micround servers."""

        def on_found(url, name):
            if not self._connected and not self._connecting:
                self._status_queue.put(("server_found", url))

        def on_lost(name):
            pass

        self._discovery = MicroundDiscovery(on_found, on_lost)
        self._discovery.start()

    @objc.python_method
    def _stop_discovery(self):
        if self._discovery:
            self._discovery.stop()
            self._discovery = None


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = AppDelegate.alloc().init()

    # Accept an optional URL from the command line
    if len(sys.argv) > 1:
        url = sys.argv[1]
        if not url.startswith("ws://") and not url.startswith("wss://"):
            url = "ws://" + url
        delegate._initial_url = url
        log.info(f"Will connect to CLI-provided URL: {url}")

    app.setDelegate_(delegate)

    log.info("Micround Client started")
    log.info("Press Ctrl+C in the terminal or use the menu bar to quit.")
    app.run()


if __name__ == "__main__":
    main()
