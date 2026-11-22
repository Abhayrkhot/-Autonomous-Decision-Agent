# Stage 2
Clean archive 27b5026: 75 tests passed, 98.12% combined statement/branch coverage.
PostgreSQL 17 ran in a disposable Docker container. Verified pool exhaustion,
transaction rollback, migration checksum rejection, reconnect persistence, concurrent
writes, shared adapter contracts, and API lifespan. No database-process kill or
large-volume endurance test was performed in this stage; those remain operational
recovery work. Tests mutate migration metadata and require a disposable database.
