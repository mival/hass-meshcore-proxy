#!/usr/bin/env python3
"""BLE Pairing Setup Server for MeshCore Proxy Home Assistant add-on.

Exposes a small web UI (served via HA ingress) that lets users scan for
nearby Bluetooth devices, pair/trust/connect to a MeshCore radio, and
remove stale pairings — all without SSH access.
"""

import http.server
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse

PORT = int(os.environ.get("BLE_SETUP_PORT", 7654))

# Strict MAC address validation to prevent command injection
MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')

_scanning = False
_scan_lock = threading.Lock()


def valid_mac(addr: str) -> bool:
    return bool(MAC_RE.match(str(addr)))


def run_bt(*args: str, timeout: int = 10):
    """Run a non-interactive bluetoothctl command and return (stdout, returncode)."""
    try:
        r = subprocess.run(
            ["bluetoothctl"] + list(args),
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "timeout", 1
    except Exception as exc:
        return str(exc), 1


def parse_devices(out: str) -> list:
    """Parse 'Device <MAC> <Name>' lines returned by bluetoothctl."""
    devices = []
    for line in out.splitlines():
        parts = line.split(" ", 2)
        if len(parts) >= 3 and parts[0] == "Device":
            devices.append({"address": parts[1], "name": parts[2]})
    return devices


def do_scan(duration: int = 10) -> bool:
    """Start an interactive bluetoothctl scan for *duration* seconds."""
    global _scanning
    with _scan_lock:
        if _scanning:
            return False
        _scanning = True
    try:
        proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        proc.stdin.write("power on\nscan on\n")
        proc.stdin.flush()
        time.sleep(duration)
        proc.stdin.write("scan off\nquit\n")
        proc.stdin.flush()
        proc.wait(timeout=5)
        return True
    except Exception:
        return False
    finally:
        with _scan_lock:
            _scanning = False


# ---------------------------------------------------------------------------
# HTML UI (served at /)
# ---------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BLE Pairing Setup — MeshCore Proxy</title>
<style>
  :root{--acc:#03a9f4;--danger:#e53935;--card:#fff;--bg:#f4f4f4;--text:#212121;--sub:#757575;--border:#e0e0e0}
  @media(prefers-color-scheme:dark){:root{--card:#1e1e1e;--bg:#121212;--text:#e0e0e0;--sub:#9e9e9e;--border:#333}}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--text);padding:1rem}
  h1{font-size:1.25rem;margin-bottom:.2rem}
  p.lead{color:var(--sub);font-size:.9rem;margin-bottom:1rem}
  .card{background:var(--card);border-radius:10px;padding:1rem;margin-bottom:1rem;box-shadow:0 1px 4px rgba(0,0,0,.12)}
  h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:var(--sub);margin-bottom:.6rem}
  .row{display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:.5rem;align-items:center}
  button{border:none;padding:.45rem 1.1rem;border-radius:5px;cursor:pointer;font-size:.9rem;font-weight:500;background:var(--acc);color:#fff}
  button:disabled{opacity:.45;cursor:not-allowed}
  button.danger{background:var(--danger)}
  .badge{display:inline-block;padding:.2rem .6rem;border-radius:20px;font-size:.75rem;font-weight:600}
  .badge.on{background:#e8f5e9;color:#2e7d32}.badge.off{background:#ffebee;color:#b71c1c}
  @media(prefers-color-scheme:dark){.badge.on{background:#1b5e20;color:#a5d6a7}.badge.off{background:#7f0000;color:#ef9a9a}}
  ul.dlist{list-style:none}
  ul.dlist li{display:flex;align-items:center;justify-content:space-between;padding:.45rem 0;border-bottom:1px solid var(--border)}
  ul.dlist li:last-child{border-bottom:none}
  .dn{font-weight:500}.da{font-size:.8rem;color:var(--sub)}
  .empty{color:var(--sub);font-size:.9rem;padding:.3rem 0}
  pre#log{font-family:monospace;font-size:.8rem;white-space:pre-wrap;background:#111;color:#ccc;padding:.75rem;border-radius:6px;max-height:150px;overflow-y:auto}
  .tip{background:#e3f2fd;color:#1565c0;padding:.5rem .75rem;border-radius:5px;font-size:.85rem;margin-bottom:.75rem}
  @media(prefers-color-scheme:dark){.tip{background:#0d2137;color:#90caf9}}
</style>
</head>
<body>
<h1>Bluetooth Pairing Setup</h1>
<p class="lead">Scan for your MeshCore radio, pair it, then set <strong>connection_type: ble</strong> and fill in the MAC address in add-on options.</p>

<div class="tip">If you see <em>Failed to connect to radio</em>, the device is not paired at the OS level. Use this page to fix that.</div>

<div class="card">
  <h2>Adapter</h2>
  <div class="row">
    <span id="bt-badge" class="badge off">…</span>
    <button onclick="powerOn()">Power On</button>
    <button onclick="powerOff()" class="danger">Power Off</button>
  </div>
</div>

<div class="card">
  <h2>Scan for Nearby Devices</h2>
  <div class="row"><button id="scan-btn" onclick="startScan()">Scan (10 s)</button></div>
  <ul id="scan-list" class="dlist"><li class="empty">Press Scan to discover devices.</li></ul>
</div>

<div class="card">
  <h2>Paired Devices</h2>
  <ul id="paired-list" class="dlist"><li class="empty">Loading…</li></ul>
</div>

<div class="card">
  <h2>Log</h2>
  <pre id="log">Ready.
</pre>
</div>

<script>
"use strict";
const $log = document.getElementById('log');
function log(m){ $log.textContent += m + '\n'; $log.scrollTop = $log.scrollHeight; }
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// Determine base path for ingress compatibility.
// When served via HA ingress the page URL is .../api/hassio_ingress/{token}/
// so relative paths (no leading slash) resolve correctly.
const BASE = (function(){
  const p = window.location.pathname;
  return p.endsWith('/') ? p : p + '/';
})();

async function api(path, opts={}){
  try{
    const r = await fetch(BASE + path, opts);
    if(!r.ok) log('HTTP '+r.status+' from '+path);
    return await r.json();
  }catch(e){ log('Error: '+e); return null; }
}

async function checkBt(){
  log('[INFO] Checking Bluetooth adapter status…');
  const d = await api('api/adapter');
  if(!d) { log('[ERROR] Failed to get adapter status'); return; }
  const b = document.getElementById('bt-badge');
  if(d.powered){
    b.textContent='Powered ON';
    b.className='badge on';
    log('[INFO] Adapter '+esc(d.name)+' is powered ON');
  } else {
    b.textContent='Powered OFF';
    b.className='badge off';
    log('[INFO] Adapter '+esc(d.name)+' is powered OFF');
  }
}

async function powerOn(){
  log('[INFO] Sending power on command…');
  const d = await api('api/power',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state:'on'})});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+d?.message||'Power on complete');
  await checkBt();
}
async function powerOff(){
  log('[INFO] Sending power off command…');
  const d = await api('api/power',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state:'off'})});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+d?.message||'Power off complete');
  await checkBt();
}

async function startScan(){
  const btn = document.getElementById('scan-btn');
  btn.disabled = true; btn.textContent = 'Scanning…';
  log('[INFO] Starting BLE device scan (10 seconds)…');
  const d = await api('api/scan',{method:'POST'});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+d?.message||'Scan done');
  btn.disabled=false; btn.textContent='Scan (10 s)';
  await loadDevices();
}

function deviceRow(dev, action, label, cls=''){
  return `<li><div><div class="dn">${esc(dev.name)}</div><div class="da">${esc(dev.address)}</div></div>`
       + `<button class="${cls}" onclick="${action}('${esc(dev.address)}','${esc(dev.name)}')">${label}</button></li>`;
}

async function loadDevices(){
  log('[INFO] Loading available devices…');
  const d = await api('api/devices');
  const ul = document.getElementById('scan-list');
  if(!d||!d.devices.length){
    log('[INFO] No devices found');
    ul.innerHTML='<li class="empty">No devices found.</li>';
    return;
  }
  log('[SUCCESS] Found '+d.devices.length+' device(s)');
  ul.innerHTML = d.devices.map(dev => deviceRow(dev,'pairDevice','Pair')).join('');
}

async function loadPaired(){
  log('[INFO] Loading paired devices…');
  const d = await api('api/paired');
  const ul = document.getElementById('paired-list');
  if(!d||!d.devices.length){
    log('[INFO] No paired devices');
    ul.innerHTML='<li class="empty">No paired devices.</li>';
    return;
  }
  log('[SUCCESS] Found '+d.devices.length+' paired device(s)');
  ul.innerHTML = d.devices.map(dev => deviceRow(dev,'removeDevice','Remove','danger')).join('');
}

async function pairDevice(addr, name){
  log('[INFO] Pairing '+esc(name)+' ('+esc(addr)+')…');
  const d = await api('api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({address:addr})});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+d?.message||'Pairing complete');
  await loadPaired(); await loadDevices();
}

async function removeDevice(addr, name){
  if(!confirm('Remove '+name+' ('+addr+')?')) { log('[INFO] Remove cancelled'); return; }
  log('[INFO] Removing device '+esc(addr)+' ('+esc(name)+')…');
  const d = await api('api/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({address:addr})});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+d?.message||'Device removed');
  await loadPaired();
}

log('[INFO] Page loaded. Checking Bluetooth adapter…');
checkBt(); loadDevices(); loadPaired();
</script>
</body>
</html>
"""


class BLESetupHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default access log

    def send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n)) if n > 0 else {}

    # ------------------------------------------------------------------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        print(f"[GET] {path}", flush=True)

        if path in ("/", "/index.html"):
            print(f"[GET] Serving HTML UI", flush=True)
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/adapter":
            print(f"[API] Querying adapter status", flush=True)
            out, _ = run_bt("show")
            powered = "Powered: yes" in out
            name = "hci0"
            for line in out.splitlines():
                if "Name:" in line:
                    name = line.split("Name:", 1)[1].strip()
                    break
            print(f"[API] Adapter {name}: powered={powered}", flush=True)
            self.send_json({"powered": powered, "name": name})

        elif path == "/api/devices":
            print(f"[API] Querying available devices", flush=True)
            out, _ = run_bt("devices")
            devices = parse_devices(out)
            print(f"[API] Found {len(devices)} available device(s)", flush=True)
            self.send_json({"devices": devices})

        elif path == "/api/paired":
            print(f"[API] Querying paired devices", flush=True)
            out, _ = run_bt("devices", "Paired")
            devices = parse_devices(out)
            print(f"[API] Found {len(devices)} paired device(s)", flush=True)
            self.send_json({"devices": devices})

        else:
            print(f"[GET] Not found: {path}", flush=True)
            self.send_json({"error": "Not found"}, 404)

    # ------------------------------------------------------------------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.read_json()
        print(f"[POST] {path}", flush=True)

        if path == "/api/power":
            state = "on" if body.get("state") == "on" else "off"
            print(f"[API] Powering {state}…", flush=True)
            out, rc = run_bt("power", state)
            result = {"ok": rc == 0, "message": out or f"Power {state}"}
            print(f"[API] Power {state}: rc={rc}, ok={result['ok']}", flush=True)
            self.send_json(result)

        elif path == "/api/scan":
            print(f"[API] Starting device scan…", flush=True)
            if _scanning:
                print(f"[API] Scan already running", flush=True)
                self.send_json({"ok": False, "message": "Scan already running"})
                return
            ok = do_scan(10)
            msg = "Scan complete" if ok else "Scan failed"
            print(f"[API] Scan finished: ok={ok}", flush=True)
            self.send_json({"ok": ok, "message": msg})

        elif path == "/api/pair":
            addr = body.get("address", "")
            print(f"[API] Pair request for {addr}", flush=True)
            if not valid_mac(addr):
                print(f"[API] Invalid MAC address: {addr}", flush=True)
                self.send_json({"ok": False, "message": "Invalid MAC address"}, 400)
                return
            print(f"[API] Ensuring power is on…", flush=True)
            run_bt("power", "on")
            print(f"[API] Attempting to pair {addr}…", flush=True)
            _, rc = run_bt("pair", addr, timeout=30)
            if rc == 0:
                print(f"[API] Pair successful for {addr}", flush=True)
                print(f"[API] Trusting {addr}…", flush=True)
                run_bt("trust", addr)
                print(f"[API] Connecting to {addr}…", flush=True)
                run_bt("connect", addr, timeout=15)
                msg = f"Paired and trusted {addr}"
            else:
                print(f"[API] Pair failed/skipped for {addr} (rc={rc})", flush=True)
                msg = f"Pairing finished for {addr} (may already be paired — check paired devices)"
            self.send_json({"ok": rc == 0, "message": msg})

        elif path == "/api/remove":
            addr = body.get("address", "")
            print(f"[API] Remove request for {addr}", flush=True)
            if not valid_mac(addr):
                print(f"[API] Invalid MAC address: {addr}", flush=True)
                self.send_json({"ok": False, "message": "Invalid MAC address"}, 400)
                return
            print(f"[API] Removing {addr}…", flush=True)
            out, rc = run_bt("remove", addr)
            result = {"ok": rc == 0, "message": out or f"Removed {addr}"}
            print(f"[API] Remove {addr}: rc={rc}, ok={result['ok']}", flush=True)
            self.send_json(result)

        else:
            print(f"[POST] Not found: {path}", flush=True)
            self.send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), BLESetupHandler) as srv:
        print(f"BLE setup server listening on port {PORT}", flush=True)
        srv.serve_forever()
