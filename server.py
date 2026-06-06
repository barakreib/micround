#!/usr/bin/env python3
"""
Micround Server v2 — Menu bar app that streams a USB microscope feed via WebSocket.

Run this on the Mac with the USB microscope plugged in.
Usage:
    python3 server.py
"""

import asyncio
import json
import logging
import socket
import subprocess
import threading
import time

import cv2
import rumps
import websockets
from zeroconf import ServiceInfo, Zeroconf

# ── Configuration ─────────────────────────────────────────────────────────────

WS_PORT = 9876
SERVICE_TYPE = "_micround._tcp.local."
SERVICE_NAME = "Micround._micround._tcp.local."

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("micround-server")


# ── Utilities ─────────────────────────────────────────────────────────────────

def get_local_ip():
    """Return the local LAN IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def copy_to_clipboard(text):
    """Copy a string to the macOS clipboard."""
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
    except Exception as e:
        log.warning(f"Could not copy to clipboard: {e}")


# ── Camera Capture ────────────────────────────────────────────────────────────

class CameraCapture:
    """
    Continuously reads frames from a camera via OpenCV,
    encodes them as JPEG, and stores the latest frame.
    """

    def __init__(self):
        self.frame = None          # Latest JPEG bytes
        self.lock = threading.Lock()
        self.running = False
        self.quality = 80          # JPEG quality (1-100)
        self.fps = 30              # Target capture FPS
        self.camera_index = 0
        self._cap = None
        self._thread = None

    # ── public API ────────────────────────────────────────────────────────

    def start(self, camera_index=0):
        """Open the camera and begin capturing frames in a background thread."""
        self.camera_index = camera_index
        self._cap = cv2.VideoCapture(camera_index)

        # If the requested index doesn't work, try the other common one
        if not self._cap.isOpened():
            alt = 1 if camera_index == 0 else 0
            log.info(f"Camera {camera_index} unavailable, trying {alt}…")
            self._cap = cv2.VideoCapture(alt)
            if self._cap.isOpened():
                self.camera_index = alt

        if not self._cap.isOpened():
            log.error("No camera found.")
            return False

        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(f"Camera capture started (index {self.camera_index})")
        return True

    def stop(self):
        """Stop the capture thread and release the camera."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._cap:
            self._cap.release()
            self._cap = None
        with self.lock:
            self.frame = None
        log.info("Camera capture stopped")

    def get_frame(self):
        """Return the most recently captured JPEG frame (bytes), or None."""
        with self.lock:
            return self.frame

    # ── private ───────────────────────────────────────────────────────────

    def _loop(self):
        while self.running:
            ret, raw = self._cap.read()
            if ret:
                params = [cv2.IMWRITE_JPEG_QUALITY, self.quality]
                ok, buf = cv2.imencode(".jpg", raw, params)
                if ok:
                    with self.lock:
                        self.frame = buf.tobytes()
            # Pace ourselves to the configured FPS
            time.sleep(1.0 / max(1, self.fps))


# ── WebSocket Streaming Server ────────────────────────────────────────────────

class StreamServer:
    """
    A WebSocket server that broadcasts the latest camera frame
    to every connected client at the configured frame rate.
    """

    def __init__(self, capture: CameraCapture):
        self.capture = capture
        self.clients: set = set()
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info(f"WebSocket server starting on port {WS_PORT}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        log.info("WebSocket server stopped")

    @property
    def client_count(self):
        return len(self.clients)

    # ── private ───────────────────────────────────────────────────────────

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except Exception as e:
            log.error(f"Server error: {e}")
        finally:
            loop.close()

    async def _serve(self):
        async with websockets.serve(
            self._handler,
            "0.0.0.0",
            WS_PORT,
            max_size=10_000_000,  # 10 MB max message
        ):
            # This loop keeps the server alive and broadcasts frames
            while self._running:
                frame = self.capture.get_frame()
                if frame and self.clients:
                    websockets.broadcast(self.clients, frame)
                await asyncio.sleep(1.0 / max(1, self.capture.fps))

    async def _handler(self, websocket):
        """Handle a single WebSocket client connection."""
        addr = websocket.remote_address
        self.clients.add(websocket)
        log.info(f"Client connected: {addr}  (total: {len(self.clients)})")

        try:
            # Listen for control messages from the client
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type")
                    if msg_type == "set_quality":
                        self.capture.quality = max(10, min(100, int(data["value"])))
                        log.info(f"Quality → {self.capture.quality}%")
                    elif msg_type == "set_fps":
                        self.capture.fps = max(1, min(60, int(data["value"])))
                        log.info(f"FPS → {self.capture.fps}")
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            log.info(f"Client disconnected: {addr}  (total: {len(self.clients)})")


# ── Bonjour / mDNS Service ────────────────────────────────────────────────────

class BonjourService:
    """Advertises the Micround server on the LAN so clients can auto-discover it."""

    def __init__(self):
        self._zeroconf = None
        self._info = None

    def register(self):
        local_ip = get_local_ip()
        self._info = ServiceInfo(
            SERVICE_TYPE,
            SERVICE_NAME,
            addresses=[socket.inet_aton(local_ip)],
            port=WS_PORT,
            properties={"version": "2.0", "name": socket.gethostname()},
        )
        self._zeroconf = Zeroconf()
        self._zeroconf.register_service(self._info)
        log.info(f"Bonjour service registered on {local_ip}:{WS_PORT}")

    def unregister(self):
        if self._zeroconf and self._info:
            self._zeroconf.unregister_service(self._info)
            self._zeroconf.close()
            self._zeroconf = None
            self._info = None
            log.info("Bonjour service unregistered")


# ── Ngrok Tunnel ──────────────────────────────────────────────────────────────

class NgrokTunnel:
    """
    Creates a public tunnel so clients outside the LAN can connect.
    Uses the free tier of ngrok.
    """

    def __init__(self):
        self.public_url = None

    def connect(self):
        """Start a tunnel and return the public WebSocket URL, or None on failure."""
        try:
            from pyngrok import ngrok

            tunnel = ngrok.connect(WS_PORT)
            # Translate the HTTP URL to a WebSocket URL
            raw = tunnel.public_url
            self.public_url = raw.replace("https://", "wss://").replace(
                "http://", "ws://"
            )
            log.info(f"Ngrok tunnel opened: {self.public_url}")
            return self.public_url
        except ImportError:
            log.error("pyngrok is not installed.  pip install pyngrok")
            return None
        except Exception as e:
            log.error(f"Ngrok failed: {e}")
            return None

    def disconnect(self):
        try:
            from pyngrok import ngrok

            ngrok.kill()
        except Exception:
            pass
        self.public_url = None
        log.info("Ngrok tunnel closed")


# ── Menu Bar Application ──────────────────────────────────────────────────────

class MicroundServer(rumps.App):
    """
    macOS menu-bar application that ties together camera capture,
    WebSocket streaming, Bonjour advertisement, and Ngrok tunneling.
    """

    def __init__(self):
        super().__init__("🔬", quit_button=None)

        # Core components
        self.capture = CameraCapture()
        self.server = StreamServer(self.capture)
        self.bonjour = BonjourService()
        self.ngrok = NgrokTunnel()

        self.local_ip = get_local_ip()
        self._streaming = False

        # ── Build the menu ────────────────────────────────────────────────

        # Status line (non-clickable)
        self._status = rumps.MenuItem("Status: Idle")
        self._status.set_callback(None)

        # Start / Stop toggle
        self._toggle = rumps.MenuItem(
            "▶  Start Streaming", callback=self._toggle_streaming
        )

        # Quality submenu
        self._quality_menu = rumps.MenuItem("Quality")
        self._quality_values = {
            "Low (40%)": 40,
            "Medium (60%)": 60,
            "High (80%)": 80,
            "Max (100%)": 100,
        }
        for label, val in self._quality_values.items():
            item = rumps.MenuItem(label, callback=self._set_quality)
            item.state = label == "High (80%)"
            self._quality_menu[label] = item

        # FPS submenu
        self._fps_menu = rumps.MenuItem("FPS")
        self._fps_values = {"15 fps": 15, "30 fps": 30}
        for label, val in self._fps_values.items():
            item = rumps.MenuItem(label, callback=self._set_fps)
            item.state = label == "30 fps"
            self._fps_menu[label] = item

        # Camera submenu
        self._camera_menu = rumps.MenuItem("Camera")
        self._camera_values = {
            "Camera 0 (default)": 0,
            "Camera 1": 1,
            "Camera 2": 2,
        }
        for label, idx in self._camera_values.items():
            item = rumps.MenuItem(label, callback=self._set_camera)
            item.state = label == "Camera 0 (default)"
            self._camera_menu[label] = item

        # URL display lines (non-clickable)
        self._local_url_item = rumps.MenuItem(
            f"Local: ws://{self.local_ip}:{WS_PORT}"
        )
        self._local_url_item.set_callback(None)

        self._remote_url_item = rumps.MenuItem("Remote: Not connected")
        self._remote_url_item.set_callback(None)

        # Action buttons
        self._copy_local = rumps.MenuItem(
            "📋 Copy Local URL", callback=self._copy_local_url
        )
        self._copy_remote = rumps.MenuItem(
            "📋 Copy Remote URL", callback=self._copy_remote_url
        )
        self._ngrok_toggle = rumps.MenuItem(
            "Enable Remote Access", callback=self._toggle_ngrok
        )
        self._quit_item = rumps.MenuItem("Quit", callback=self._quit_app)

        # Assemble
        self.menu = [
            self._status,
            None,
            self._toggle,
            None,
            self._quality_menu,
            self._fps_menu,
            self._camera_menu,
            None,
            self._local_url_item,
            self._remote_url_item,
            None,
            self._copy_local,
            self._copy_remote,
            self._ngrok_toggle,
            None,
            self._quit_item,
        ]

    # ── Streaming ─────────────────────────────────────────────────────────

    def _toggle_streaming(self, sender):
        if self._streaming:
            self._stop_streaming()
        else:
            self._start_streaming()

    def _start_streaming(self):
        if not self.capture.start(camera_index=self._current_camera_index()):
            rumps.alert(
                title="Camera Error",
                message=(
                    "No camera found.\n\n"
                    "Make sure the USB microscope is plugged in and "
                    "camera access is allowed in System Settings → "
                    "Privacy & Security → Camera."
                ),
            )
            return

        self.server.start()
        self.bonjour.register()

        self._streaming = True
        self.title = "📡"
        self._status.title = "Status: Streaming"
        self._toggle.title = "⏹  Stop Streaming"
        log.info("Streaming started")

    def _stop_streaming(self):
        self.bonjour.unregister()
        self.server.stop()
        self.capture.stop()

        self._streaming = False
        self.title = "🔬"
        self._status.title = "Status: Idle"
        self._toggle.title = "▶  Start Streaming"
        log.info("Streaming stopped")

    # ── Settings ──────────────────────────────────────────────────────────

    def _set_quality(self, sender):
        value = self._quality_values.get(sender.title)
        if value is not None:
            self.capture.quality = value
            for lbl in self._quality_values:
                self._quality_menu[lbl].state = lbl == sender.title

    def _set_fps(self, sender):
        value = self._fps_values.get(sender.title)
        if value is not None:
            self.capture.fps = value
            for lbl in self._fps_values:
                self._fps_menu[lbl].state = lbl == sender.title

    def _set_camera(self, sender):
        idx = self._camera_values.get(sender.title)
        if idx is not None:
            for lbl in self._camera_values:
                self._camera_menu[lbl].state = lbl == sender.title
            # If already streaming, restart capture with the new camera
            if self._streaming:
                self.capture.stop()
                if not self.capture.start(camera_index=idx):
                    rumps.alert(
                        title="Camera Error",
                        message=f"Camera {idx} is not available.",
                    )

    def _current_camera_index(self):
        for lbl, idx in self._camera_values.items():
            if self._camera_menu[lbl].state:
                return idx
        return 0

    # ── URL Copy ──────────────────────────────────────────────────────────

    def _copy_local_url(self, sender):
        url = f"ws://{self.local_ip}:{WS_PORT}"
        copy_to_clipboard(url)
        rumps.notification("Micround", "Copied!", "Local URL copied to clipboard")

    def _copy_remote_url(self, sender):
        if self.ngrok.public_url:
            copy_to_clipboard(self.ngrok.public_url)
            rumps.notification(
                "Micround", "Copied!", "Remote URL copied to clipboard"
            )
        else:
            rumps.alert(
                title="No Remote URL",
                message="Enable Remote Access first.",
            )

    # ── Ngrok ─────────────────────────────────────────────────────────────

    def _toggle_ngrok(self, sender):
        if self.ngrok.public_url:
            # Disable
            self.ngrok.disconnect()
            self._remote_url_item.title = "Remote: Not connected"
            sender.title = "Enable Remote Access"
        else:
            # Enable
            url = self.ngrok.connect()
            if url:
                self._remote_url_item.title = f"Remote: {url}"
                sender.title = "Disable Remote Access"
            else:
                rumps.alert(
                    title="Ngrok Error",
                    message=(
                        "Could not start ngrok.\n\n"
                        "Make sure it's installed and configured:\n"
                        "  brew install ngrok\n"
                        "  ngrok config add-authtoken YOUR_TOKEN\n\n"
                        "Sign up free at https://dashboard.ngrok.com/signup"
                    ),
                )

    # ── Quit ──────────────────────────────────────────────────────────────

    def _quit_app(self, sender):
        if self._streaming:
            self._stop_streaming()
        if self.ngrok.public_url:
            self.ngrok.disconnect()
        rumps.quit_application()


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info(f"Local IP: {get_local_ip()}")
    log.info(f"WebSocket port: {WS_PORT}")
    MicroundServer().run()
