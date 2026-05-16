# Seed data folder (`data/`)

Place database dumps and files here for **LEco DevOps → Hosted apps → Import data**, or import manually with the CLI examples below.

See [Hosted app data import](../../../docs/help/13-hosted-app-data-import.md) for the full guide.

## Layout

```
data/
  manifest.yaml       # optional — explicit import plan
  mongo/<database>/   # mongodump output (one folder per DB; multiple after a full-server dump)
  mysql/<database>.sql
  postgres/<database>.sql
  redis/dump.rdb
  d1/<database>.sql
  r2/<bucket-name>/...
  kv/<namespace>/keys.json
  files/uploads/...
```

## MongoDB — one database

### Pipe (fast, no `data/` folder)

```bash
mongodump --uri="mongodb://localhost:27017" --db=<source-database> --archive \
  | mongorestore --uri="mongodb://127.0.0.1:<host-port>/<target-database>" --archive --drop
```

### Dump into `data/mongo/`

```bash
mkdir -p mongo
mongodump --uri="mongodb://localhost:27017" --db=<source-database> --out="$(pwd)/mongo"
leco-devops import-data --manifest ../leco.app.yaml --reimport
```

## MongoDB — full server (all databases)

Omit `--db` on `mongodump` to copy **every** database on the source instance (`admin`, app DBs, etc.). **Import data** auto-detects each `mongo/<database>/` subfolder.

### Pipe

```bash
mongodump --uri="mongodb://localhost:27017" --archive \
  | mongorestore --uri="mongodb://127.0.0.1:<host-port>" --archive --drop
```

### Dump into `data/mongo/`

```bash
mkdir -p mongo
mongodump --uri="mongodb://localhost:27017" --out="$(pwd)/mongo"
leco-devops import-data --manifest ../leco.app.yaml --reimport
```

Use **Attached services** for `<host-port>` and database names. If authentication is required, add URI options or env — **do not commit passwords**.

## MongoDB — docker exec only

```bash
# One database (mongodump creates /tmp/seed/<database>/*.bson)
mongodump --uri="mongodb://localhost:27017" --db=<source-database> --out=/tmp/seed
docker cp /tmp/seed/<source-database> <container-name>:/tmp/seed-db
docker exec <container-name> mongorestore --drop --db=<target-database> /tmp/seed-db

# Full server (mongodump creates /tmp/seed/<database>/ per DB)
mongodump --uri="mongodb://localhost:27017" --out=/tmp/seed
docker cp /tmp/seed <container-name>:/tmp/seed
docker exec <container-name> mongorestore --drop /tmp/seed
```

After changing compose `ports:`, **recreate** the mongo service (`docker compose up -d mongo`) so the host port is bound.
