ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.22
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip bluez \
    && python3 -m venv /opt/meshcore-proxy \
    && /opt/meshcore-proxy/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/meshcore-proxy/bin/pip install --no-cache-dir meshcore-proxy

ENV PATH="/opt/meshcore-proxy/bin:${PATH}"

COPY run.sh /run.sh
COPY ble_setup.py /ble_setup.py
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]