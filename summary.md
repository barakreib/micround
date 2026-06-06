# Micround v2 — Project Summary

## Goal
Stream a live video feed from a USB microscope connected to a Desktop Mac and display it as an **animated desktop wallpaper** on any Mac — either the same machine or a wirelessly connected MacBook.

## What Changed from v1

| Aspect | v1 | v2 |
|--------|----|----|
| Streaming protocol | MJPEG over HTTP (Flask) | WebSocket (binary JPEG frames) |
| Frame rendering | WKWebView (browser engine) | Native NSImageView (PyObjC) |
| Server UI | Terminal only | macOS menu bar app (rumps) |
| Client UI | Terminal only | macOS menu bar app (PyObjC) |
| LAN discovery | Manual URL copy-paste | Automatic via Bonjour/mDNS |
| Remote access | Ngrok (always on) | Ngrok (toggleable from menu bar) |
| Reconnection | None — freezes on disconnect | Auto-reconnect with exponential backoff |
| Quality controls | None | Adjustable JPEG quality, FPS, camera selection |
| Single-machine mode | Not supported | Works by connecting to localhost |

## Key Components
- **server.py** — Menu bar app: camera capture → WebSocket broadcast → Bonjour → Ngrok
- **client.py** — Menu bar app: Bonjour discovery → WebSocket client → desktop wallpaper
- **setup.sh** — One-command setup for both machines
