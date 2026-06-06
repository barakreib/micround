# Micround v2 — Microscope Background Streamer

Stream a live video feed from a USB microscope and display it as an **animated desktop wallpaper** on any Mac — over LAN or the internet.

---

## Features

| Feature | Description |
|---------|-------------|
| 🖥️ **Live wallpaper** | Microscope feed renders as your actual desktop background (behind all windows) |
| 📡 **WebSocket streaming** | Low-latency, bidirectional protocol with backpressure handling |
| 🔍 **Auto-discovery** | Client finds the server automatically on the same Wi-Fi via Bonjour/mDNS |
| 🌐 **Cross-network** | Connect from anywhere using a free Ngrok tunnel |
| 🔬 **Menu bar apps** | Both server and client run as macOS menu bar apps — no Terminal window needed after setup |
| 🔄 **Auto-reconnect** | Client reconnects automatically if the connection drops (exponential backoff) |
| ⚙️ **Quality controls** | Adjust JPEG quality, FPS, and camera selection from the server menu bar |
| 💻 **Single-machine** | Run both on the same Mac if you want the microscope as your own wallpaper |

---

## What You Need

| Item | Server Mac (Microscope) | Client Mac (Wallpaper) |
|------|:-----------------------:|:----------------------:|
| macOS 12+ | ✅ | ✅ |
| Python 3.9+ | ✅ | ✅ |
| USB Microscope | ✅ | ❌ |
| Same Wi-Fi **or** internet | ✅ | ✅ |
| Ngrok account (free) | Only for remote use | ❌ |

---

## Quick Setup (Both Machines)

> [!IMPORTANT]
> Run this setup on **both** the Server Mac and the Client Mac.

### 1. Get the project code

```bash
cd ~/codingprojects/micround
```

If you don't have the code yet, clone it:
```bash
cd ~/codingprojects
git clone https://github.com/barakreib/micround.git
cd micround
```

### 2. Run the setup script

```bash
bash setup.sh
```

This installs Homebrew, Python 3, Git, creates a virtual environment, and installs all dependencies — automatically.

### 3. Set up Ngrok (Server Mac only — optional)

Only needed if you want to stream across different Wi-Fi networks.

```bash
brew install ngrok
```

Sign up for a free account at [ngrok.com](https://dashboard.ngrok.com/signup), copy your authtoken, then:

```bash
ngrok config add-authtoken YOUR_AUTHTOKEN_HERE
```

---

## Running the Application

### Server (Mac with the microscope)

```bash
cd ~/codingprojects/micround
source venv/bin/activate
python3 server.py
```

A 🔬 icon appears in your menu bar. Click it and select **▶ Start Streaming**.

The server will:
- Open the USB microscope camera
- Start a WebSocket server on port 9876
- Advertise itself on the LAN via Bonjour

### Client (Mac for the wallpaper)

```bash
cd ~/codingprojects/micround
source venv/bin/activate
python3 client.py
```

A 🔬 icon appears in your menu bar. The client will:
- Automatically scan for a Micround server on the LAN
- Connect and start displaying the microscope feed as your desktop wallpaper

#### Manual connection

If auto-discovery doesn't find the server (e.g., different networks), you can:

**Option A — Click the menu bar icon:**
- Select **🔗 Enter URL manually…**
- Paste the URL shown in the server's menu bar

**Option B — Provide the URL on the command line:**
```bash
python3 client.py ws://192.168.1.5:9876          # LAN
python3 client.py wss://abc123.ngrok-free.app    # Remote via Ngrok
```

### Using Ngrok (Remote Access)

On the **Server Mac**:
1. Click the 🔬 menu bar icon
2. Select **Enable Remote Access**
3. The remote URL appears in the menu (e.g., `wss://abc123.ngrok-free.app`)
4. Click **📋 Copy Remote URL** and send it to the client user

On the **Client Mac**:
1. Click the 🔬 menu bar icon
2. Select **🔗 Enter URL manually…**
3. Paste the URL and click **Connect**

### To Stop

- **Server**: Click 🔬 → **⏹ Stop Streaming** → **Quit**
- **Client**: Click 🔬 → **Quit**

Or press `Ctrl+C` in the terminal.

---

## Server Menu Bar Guide

| Menu Item | What It Does |
|-----------|-------------|
| Status: … | Shows whether the server is streaming or idle |
| ▶ Start / ⏹ Stop Streaming | Toggle the camera and WebSocket server |
| Quality ▸ | Set JPEG compression: Low (40%), Medium (60%), High (80%), Max (100%) |
| FPS ▸ | Set the frame rate: 15 or 30 fps |
| Camera ▸ | Switch between connected cameras (0, 1, 2) |
| Local: ws://… | The LAN URL for the stream |
| Remote: wss://… | The Ngrok URL (if remote access is enabled) |
| 📋 Copy Local/Remote URL | Copies the URL to your clipboard |
| Enable/Disable Remote Access | Toggle Ngrok tunnel on/off |
| Quit | Stop everything and exit |

## Client Menu Bar Guide

| Menu Item | What It Does |
|-----------|-------------|
| Status: … | Shows connection state (Searching, Connected, Reconnecting, etc.) |
| Server: … | Shows the URL of the current server |
| 🔍 Scan for servers | Re-scan the LAN for Micround servers |
| 🔗 Enter URL manually… | Open a dialog to type/paste a server URL |
| Disconnect / Reconnect | Toggle the connection |
| Quit | Disconnect and exit |

---

## Day-to-Day Usage (Quick Reference)

Once setup is done, all you need each time:

**Server:**
```bash
cd ~/codingprojects/micround
source venv/bin/activate
python3 server.py
# Click 🔬 → Start Streaming
```

**Client:**
```bash
cd ~/codingprojects/micround
source venv/bin/activate
python3 client.py
# Auto-connects on the same Wi-Fi!
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `python3: command not found` | Run `brew install python` |
| `pip: command not found` | Activate the venv: `source venv/bin/activate` |
| Camera not found | Try switching cameras in the server menu (Camera ▸). Also check System Settings → Privacy & Security → Camera → allow Terminal |
| Client shows black screen | Make sure the server is streaming (icon should be 📡 not 🔬) |
| Auto-discovery doesn't work | Both Macs must be on the same Wi-Fi network. Try manual URL entry instead |
| Ngrok error | Re-run `ngrok config add-authtoken YOUR_TOKEN`. Make sure you signed up at [ngrok.com](https://dashboard.ngrok.com/signup) |
| `ModuleNotFoundError` | Activate the venv and re-run `pip install -r requirements.txt` |
| Stream is laggy over Ngrok | Lower the quality in the server menu (Quality ▸ Low) and reduce FPS |
| Permission denied for camera | Go to System Settings → Privacy & Security → Camera → allow Terminal |

---

## Architecture

```
┌──────────────────────────────────────┐      ┌──────────────────────────────────────┐
│         SERVER (Desktop Mac)         │      │         CLIENT (MacBook)             │
│                                      │      │                                      │
│  USB Microscope                      │      │  ┌──────────────────────────────┐    │
│       │                              │      │  │  Desktop-Level NSWindow      │    │
│       ▼                              │      │  │  ┌──────────────────────┐    │    │
│  OpenCV Capture Thread               │      │  │  │   NSImageView        │    │    │
│       │                              │      │  │  │   (JPEG → NSImage)   │    │    │
│       ▼                              │      │  │  └──────────────────────┘    │    │
│  JPEG-encoded frame                  │      │  └──────────────────────────────┘    │
│       │                              │      │           ▲                          │
│       ▼                              │      │           │ (main thread)            │
│  WebSocket Server (:9876)  ─────────────────────▶  WebSocket Client               │
│       │                              │      │       (asyncio background thread)    │
│       │                              │      │           ▲                          │
│  Bonjour/mDNS  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ▶  Bonjour Discovery               │
│       │                              │      │                                      │
│  Ngrok Tunnel (optional)  ──────────────────────▶  (or manual URL entry)          │
│                                      │      │                                      │
│  🔬 Menu Bar                         │      │  🔬 Menu Bar                         │
└──────────────────────────────────────┘      └──────────────────────────────────────┘
```

**Streaming protocol:** WebSocket with binary JPEG frames (server → client) and JSON control messages (bidirectional).
