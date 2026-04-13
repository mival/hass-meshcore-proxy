#!/usr/bin/env python3
"""BLE Pairing Setup Server for MeshCore Proxy Home Assistant add-on.

Exposes a small web UI (served via HA ingress) that lets users scan for
nearby Bluetooth devices, pair/trust/connect to a MeshCore radio, and
remove stale pairings - all without SSH access.
"""

import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse

PORT = int(os.environ.get("BLE_SETUP_PORT", 7654))

# Strict MAC address validation to prevent command injection
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

_scanning = False
_scan_lock = threading.Lock()


def valid_mac(addr: str) -> bool:
    return bool(MAC_RE.match(str(addr)))


def log_info(msg: str) -> None:
    """Log an info message to stdout (captured by HA supervisor)."""
    print(f"[INFO] {msg}", flush=True)


def log_error(msg: str) -> None:
    """Log an error message to stderr (captured by HA supervisor)."""
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)


def run_bt(*args: str, timeout: int = 10):
    """Run a non-interactive bluetoothctl command and return (stdout, returncode)."""
    cmd_str = " ".join(args)
    log_info(f"[BLUETOOTHCTL] Running: bluetoothctl {cmd_str}")
    try:
        r = subprocess.run(
            ["bluetoothctl"] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        log_info(
            f"[BLUETOOTHCTL] Command completed: rc={r.returncode}, "
            f"output={r.stdout.strip()[:100]}"
        )
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        log_error(
            f"[BLUETOOTHCTL] Command timed out after {timeout}s: bluetoothctl {cmd_str}"
        )
        return "timeout", 1
    except Exception as exc:
        log_error(f"[BLUETOOTHCTL] Command failed: {exc}")
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


def pair_with_prompts(addr: str, pin: str, timeout: int = 40) -> tuple[bool, str]:
    """Pair using interactive bluetoothctl and reply to PIN/passkey prompts."""
    proc = subprocess.Popen(
        ["bluetoothctl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def send(cmd: str) -> None:
        if proc.stdin is None:
            return
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        log_info(f"[API] bluetoothctl << {cmd}")

    output_lines = []
    send("agent KeyboardDisplay")
    send("default-agent")
    send(f"pair {addr}")

    deadline = time.time() + timeout
    paired = False
    pin_sent = False

    while time.time() < deadline:
        if proc.stdout is None:
            break
        line = proc.stdout.readline()
        if line == "":
            if proc.poll() is not None:
                break
            time.sleep(0.1)
            continue

        out = line.strip()
        output_lines.append(out)
        lower = out.lower()

        if "pairing successful" in lower or "already paired" in lower:
            paired = True
            break

        if "confirm passkey" in lower or "confirm yes/no" in lower:
            send("yes")

        if (
            "enter pin" in lower
            or "pin code" in lower
            or "passkey" in lower
            or "request passkey" in lower
            or "input pin" in lower
        ) and pin and not pin_sent:
            send(pin)
            pin_sent = True

    if proc.poll() is None:
        send("quit")
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    output = "\n".join(output_lines)
    return paired, output


# ---------------------------------------------------------------------------
# HTML UI (served at /)
# ---------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BLE Pairing Setup - MeshCore Proxy</title>
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
  input{padding:.45rem .65rem;border-radius:5px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:.9rem;min-width:220px}
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
    <span id="bt-badge" class="badge off">...</span>
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
  <h2>Pairing Code (Optional)</h2>
  <div class="row">
    <input id="pair-pin" type="text" placeholder="Enter pairing code" autocomplete="off" inputmode="numeric" />
  </div>
  <p class="empty">Use this only for devices that ask for a PIN/passkey during pairing.</p>
</div>

<div class="card">
  <h2>Paired Devices</h2>
  <ul id="paired-list" class="dlist"><li class="empty">Loading...</li></ul>
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
  log('[INFO] Checking Bluetooth adapter status...');
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
  log('[INFO] Sending power on command...');
  const d = await api('api/power',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state:'on'})});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+(d?.message||'Power on complete'));
  await checkBt();
}

async function powerOff(){
  log('[INFO] Sending power off command...');
  const d = await api('api/power',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state:'off'})});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+(d?.message||'Power off complete'));
  await checkBt();
}

async function startScan(){
  const btn = document.getElementById('scan-btn');
  btn.disabled = true; btn.textContent = 'Scanning...';
  log('[INFO] Starting BLE device scan (10 seconds)...');
  const d = await api('api/scan',{method:'POST'});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+(d?.message||'Scan done'));
  btn.disabled=false; btn.textContent='Scan (10 s)';
  await loadDevices();
}

function deviceRow(dev, action, label, cls=''){
  return `<li><div><div class="dn">${esc(dev.name)}</div><div class="da">${esc(dev.address)}</div></div>`
       + `<button class="${cls}" onclick="${action}('${esc(dev.address)}','${esc(dev.name)}')">${label}</button></li>`;
}

async function loadDevices(){
  log('[INFO] Loading available devices...');
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
  log('[INFO] Loading paired devices...');
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
  const pin = document.getElementById('pair-pin')?.value?.trim() || '';
  if(pin){ log('[INFO] Pairing '+esc(name)+' ('+esc(addr)+') with PIN code...'); }
  else { log('[INFO] Pairing '+esc(name)+' ('+esc(addr)+') without PIN code...'); }
  const d = await api('api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({address:addr,pin:pin})});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+(d?.message||'Pairing complete'));
  await loadPaired(); await loadDevices();
}

async function removeDevice(addr, name){
  if(!confirm('Remove '+name+' ('+addr+')?')) { log('[INFO] Remove cancelled'); return; }
  log('[INFO] Removing device '+esc(addr)+' ('+esc(name)+')...');
  const d = await api('api/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({address:addr})});
  if(d?.ok===false) log('[ERROR] '+d?.message); else log('[SUCCESS] '+(d?.message||'Device removed'));
  await loadPaired();
}

log('[INFO] Page loaded. Checking Bluetooth adapter...');
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
        log_info(f"[GET] {path}")

        if path in ("/", "/index.html"):
            log_info("[GET] Serving HTML UI")
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/adapter":
            log_info("[API] Querying adapter status")
            out, _ = run_bt("show")
            powered = "Powered: yes" in out
            name = "hci0"
            for line in out.splitlines():
                if "Name:" in line:
                    name = line.split("Name:", 1)[1].strip()
                    break
            log_info(f"[API] Adapter {name}: powered={powered}")
            self.send_json({"powered": powered, "name": name})

        elif path == "/api/devices":
            log_info("[API] Querying available devices")
            out, _ = run_bt("devices")
            devices = parse_devices(out)
            log_info(f"[API] Found {len(devices)} available device(s)")
            self.send_json({"devices": devices})

        elif path == "/api/paired":
            log_info("[API] Querying paired devices")
            out, _ = run_bt("devices", "Paired")
            devices = parse_devices(out)
            log_info(f"[API] Found {len(devices)} paired device(s)")
            self.send_json({"devices": devices})

        else:
            log_error(f"[GET] Not found: {path}")
            self.send_json({"error": "Not found"}, 404)

    # ------------------------------------------------------------------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self.read_json()
        log_info(f"[POST] {path}")

        if path == "/api/power":
            state = "on" if body.get("state") == "on" else "off"
            log_info(f"[API] Powering {state}...")
            out, rc = run_bt("power", state)
            result = {"ok": rc == 0, "message": out or f"Power {state}"}
            log_info(f"[API] Power {state}: rc={rc}, ok={result['ok']}")
            self.send_json(result)

        elif path == "/api/scan":
            log_info("[API] Starting device scan...")
            if _scanning:
                log_error("[API] Scan already running")
                self.send_json({"ok": False, "message": "Scan already running"})
                return
            ok = do_scan(10)
            msg = "Scan complete" if ok else "Scan failed"
            log_info(f"[API] Scan finished: ok={ok}")
            self.send_json({"ok": ok, "message": msg})

        elif path == "/api/pair":
            addr = str(body.get("address", "")).strip()
            pin = str(body.get("pin", "")).strip()
            log_info(f"[API] Pair request for {addr}")
            if not valid_mac(addr):
                log_error(f"[API] Invalid MAC address: {addr}")
                self.send_json({"ok": False, "message": "Invalid MAC address"}, 400)
                return
            run_bt("power", "on")
            if pin:
                log_info(f"[API] Attempting to pair {addr} using page PIN code")
            else:
                log_info(f"[API] Attempting to pair {addr} without PIN code")
            try:
                paired, out = pair_with_prompts(addr, pin, timeout=40)
                log_info(f"[API] Pair session output: {out.strip()[:300]}")
                if paired:
                    run_bt("trust", addr)
                    run_bt("connect", addr, timeout=15)
                    msg = f"Paired and trusted {addr}"
                    log_info(f"[API] Pair successful for {addr}")
                else:
                    msg = (
                        f"Pairing finished for {addr} "
                        "(may already be paired - check paired devices)"
                    )
                    log_error(f"[API] Pair may have failed for {addr}")
                self.send_json({"ok": paired, "message": msg})
            except Exception as exc:
                log_error(f"[API] Pair exception: {exc}")
                self.send_json({"ok": False, "message": str(exc)})

        elif path == "/api/remove":
            addr = body.get("address", "")
            log_info(f"[API] Remove request for {addr}")
            if not valid_mac(addr):
                log_error(f"[API] Invalid MAC address: {addr}")
                self.send_json({"ok": False, "message": "Invalid MAC address"}, 400)
                return
            log_info(f"[API] Removing {addr}...")
            out, rc = run_bt("remove", addr)
            result = {"ok": rc == 0, "message": out or f"Removed {addr}"}
            log_info(f"[API] Remove {addr}: rc={rc}, ok={result['ok']}")
            self.send_json(result)

        else:
            log_error(f"[POST] Not found: {path}")
            self.send_json({"error": "Not found"}, 404)


if __name__ == "__main__":
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), BLESetupHandler) as srv:
        log_info(f"BLE setup server listening on port {PORT}")
        srv.serve_forever()
