ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN python3 -m pip install --no-cache-dir requests paho-mqtt==2.1.0 websockets==16.1 "paramiko>=4,<5"

COPY seiden_bridge.py /seiden_bridge.py
COPY seiden_bridge_app /seiden_bridge_app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
