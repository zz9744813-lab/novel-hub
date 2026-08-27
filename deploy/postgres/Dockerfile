FROM postgres:16-alpine

RUN mkdir -p /etc/postgresql \
    && chown postgres:postgres /etc/postgresql \
    && chmod 0755 /etc/postgresql

COPY --chown=postgres:postgres --chmod=0644 postgresql.conf /etc/postgresql/postgresql.conf
COPY --chown=postgres:postgres --chmod=0644 pg_hba.conf /etc/postgresql/pg_hba.conf
