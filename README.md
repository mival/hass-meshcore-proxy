# Home Assistant OS Add-on: MeshCore Proxy

This repository contains a Home Assistant add-on that runs `meshcore-proxy`.

## Install in Home Assistant

1. Push this repository to GitHub and update metadata in:
   - `repository.yaml`
   - `meshcore-proxy/config.yaml`
2. In Home Assistant, open **Settings -> Add-ons -> Add-on Store**.
3. Open the menu (three dots), select **Repositories**.
4. Add your repository URL.
5. Install **MeshCore Proxy**.

## Configure

The add-on now supports structured transport and network setup from the UI.

Available options:

- `connection_type`: `usb` or `ble`
- `usb_device`: serial device path for USB mode (example: `/dev/ttyUSB0`)
- `usb_baud`: serial baud rate (default: `115200`)
- `ble_address`: BLE MAC/UUID/name for BLE mode
- `tcp_host`: TCP bind host for proxy server (default: `0.0.0.0`)
- `tcp_port`: TCP bind port for proxy server (default: `5000`)
- `log_events`, `log_events_verbose`, `json_logs`, `quiet`, `debug`: logging flags
- `meshcore_proxy_args`: optional extra raw arguments appended to startup command

PIN/passkey entry for BLE pairing is now done in the ingress page (`BT Pairing`) via the `Pairing Code` field.

Example (USB):

```yaml
connection_type: usb
usb_device: /dev/ttyUSB0
usb_baud: 115200
tcp_host: 0.0.0.0
tcp_port: 5000
log_events: true
meshcore_proxy_args: ""
```

Example (BLE):

```yaml
connection_type: ble
ble_address: "12:34:56:78:90:AB"
tcp_host: 0.0.0.0
tcp_port: 5000
```

Note: Home Assistant add-ons expose TCP server settings through `tcp_host` and `tcp_port`. There is no separate radio "tcp connection" mode in meshcore-proxy; radio transport is USB serial or BLE.

BLE troubleshooting:

- Ensure `connection_type: ble` and `ble_address` is correct for your device.
- Rebuild/restart the add-on after changing BLE options.
- Keep `host_dbus: true` and `bluetooth: true` in add-on config.

## Local Development Notes

- The add-on installs `meshcore-proxy` via `pip` in the container.
- If the package name differs on PyPI, update `meshcore-proxy/Dockerfile`.