#!/usr/bin/with-contenv bashio

set -euo pipefail

CONNECTION_TYPE="$(bashio::config 'connection_type')"
USB_DEVICE="$(bashio::config 'usb_device')"
USB_BAUD="$(bashio::config 'usb_baud')"
BLE_ADDRESS="$(bashio::config 'ble_address')"
BLE_PIN="$(bashio::config 'ble_pin')"
TCP_HOST="$(bashio::config 'tcp_host')"
TCP_PORT="$(bashio::config 'tcp_port')"

LOG_EVENTS="$(bashio::config 'log_events')"
LOG_EVENTS_VERBOSE="$(bashio::config 'log_events_verbose')"
JSON_LOGS="$(bashio::config 'json_logs')"
QUIET="$(bashio::config 'quiet')"
DEBUG="$(bashio::config 'debug')"

EXTRA_ARGS="$(bashio::config 'meshcore_proxy_args')"

bashio::log.info "Starting meshcore-proxy"

CMD=(meshcore-proxy)

case "${CONNECTION_TYPE}" in
    usb)
        if bashio::var.is_empty "${USB_DEVICE}"; then
            bashio::log.fatal "connection_type is usb but usb_device is empty"
            exit 1
        fi
        CMD+=(--serial "${USB_DEVICE}")
        if ! bashio::var.is_empty "${USB_BAUD}"; then
            CMD+=(--baud "${USB_BAUD}")
        fi
        ;;
    ble)
        if bashio::var.is_empty "${BLE_ADDRESS}"; then
            bashio::log.fatal "connection_type is ble but ble_address is empty"
            exit 1
        fi

        # BLE stacks may expect either /run/dbus or /var/run/dbus. Normalize both.
        if [[ -S /run/dbus/system_bus_socket ]]; then
            export DBUS_SYSTEM_BUS_ADDRESS="unix:path=/run/dbus/system_bus_socket"
            mkdir -p /var/run/dbus
            ln -sf /run/dbus/system_bus_socket /var/run/dbus/system_bus_socket
        elif [[ -S /var/run/dbus/system_bus_socket ]]; then
            export DBUS_SYSTEM_BUS_ADDRESS="unix:path=/var/run/dbus/system_bus_socket"
        else
            bashio::log.warning "D-Bus system socket not found; BLE pairing/connection may fail"
        fi

        CMD+=(--ble "${BLE_ADDRESS}")
        if ! bashio::var.is_empty "${BLE_PIN}"; then
            CMD+=(--ble-pin "${BLE_PIN}")
        fi
        ;;
    *)
        bashio::log.fatal "Unsupported connection_type: ${CONNECTION_TYPE}. Use usb or ble"
        exit 1
        ;;
esac

if ! bashio::var.is_empty "${TCP_HOST}"; then
    CMD+=(--host "${TCP_HOST}")
fi

if ! bashio::var.is_empty "${TCP_PORT}"; then
    CMD+=(--port "${TCP_PORT}")
fi

if bashio::var.true "${LOG_EVENTS}"; then
    CMD+=(--log-events)
fi

if bashio::var.true "${LOG_EVENTS_VERBOSE}"; then
    CMD+=(--log-events-verbose)
fi

if bashio::var.true "${JSON_LOGS}"; then
    CMD+=(--json)
fi

if bashio::var.true "${QUIET}"; then
    CMD+=(--quiet)
fi

if bashio::var.true "${DEBUG}"; then
    CMD+=(--debug)
fi

if ! bashio::var.is_empty "${EXTRA_ARGS}"; then
    bashio::log.info "Using custom extra arguments from add-on options"
    eval "set -- ${EXTRA_ARGS}"
    CMD+=("$@")
fi

"${CMD[@]}" &
PROXY_PID=$!

shutdown() {
    bashio::log.info "Stopping meshcore-proxy"
    kill -TERM "${PROXY_PID}" 2>/dev/null || true
}

trap shutdown TERM INT

wait "${PROXY_PID}"