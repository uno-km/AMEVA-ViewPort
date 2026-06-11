# WebRTC Screen Share + Control

This project provides a local-network WebRTC screen sharing server with optional remote input control.

## Files
- `server.py` : Main application
- `requirements.txt` : Python dependencies
- `run_server.bat` : Windows launcher that creates `.venv`, installs dependencies, and runs the server

## Quick Start
1. Double-click `run_server.bat`
2. Enter the tokens and options
3. Open `http://<SERVER_IP>:8080` from another PC on the same network

## Notes
- Use only on devices and networks that you own or are authorized to manage.
- If the video page loads but streaming does not start, check Windows Firewall permissions for Python.
