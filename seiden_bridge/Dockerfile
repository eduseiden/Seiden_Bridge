ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN python3 -m pip install --no-cache-dir requests

COPY run.sh /
COPY seiden_bridge.py /

RUN chmod a+x /run.sh

CMD ["/run.sh"]
