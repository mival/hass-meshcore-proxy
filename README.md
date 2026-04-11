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

The add-on exposes one option:

- `meshcore_proxy_args`: optional string appended to the startup command.

Examples:

```yaml
meshcore_proxy_args: "--help"
```

## Local Development Notes

- The add-on installs `meshcore-proxy` via `pip` in the container.
- If the package name differs on PyPI, update `meshcore-proxy/Dockerfile`.