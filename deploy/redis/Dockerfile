FROM redis:7-alpine

RUN mkdir -p /usr/local/etc/redis \
    && chown redis:redis /usr/local/etc/redis \
    && chmod 0755 /usr/local/etc/redis

COPY --chown=redis:redis --chmod=0644 redis.conf /usr/local/etc/redis/redis.conf
