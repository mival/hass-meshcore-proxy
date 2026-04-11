#!/usr/bin/with-contenv bashio

set -euo pipefail

ARGS="$(bashio::config 'meshcore_proxy_args')"

bashio::log.info "Starting meshcore-proxy"

if bashio::var.is_empty "${ARGS}"; then
    exec meshcore-proxy
else
    bashio::log.info "Using custom arguments from add-on options"
    exec sh -c "meshcore-proxy ${ARGS}"
fi