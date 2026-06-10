# Source CLI

`gispulse source` is the core CLI for source definitions and staged source
artifacts.

## Dynamic Catalog

`gispulse source catalog` does not carry a hard-coded source list. It asks the
runtime `ExtensionHub` for active `PluginKind.SOURCE` records, invokes each
plugin `register()` callable, and then reads the process-wide `SOURCES`
registry. This mirrors the capability bootstrap path and keeps installed
`gispulse.data_sources` plugins as the single source of truth.

## Artifact Manifest

`gispulse source ingest` writes a persistent JSON manifest record through
`DatasetStorage`, under:

```text
<prefix>/manifest/source-artifacts/<source>/<entry>/millesime=<revision>/<scope>/manifest.json
```

The manifest records the source, entry, scope, revision, raw and stage keys,
raw and stage URIs, row count when known, destination, and status. The same
storage abstraction is used for local filesystem and S3/MinIO, so `source list`
and `source delete` can enumerate artifacts from manifest records instead of
guessing object prefixes.

For S3 bulk entries currently supported by `BulkIngestRunner`
(`TABLE_FILE` and `DOWNLOAD`), `source ingest --dest s3` delegates to that
runner and persists its manifest output in the same inventory. Other entries
use the generic `AccessSpec` dispatch path and store raw/stage artifacts
directly through `DatasetStorage`.
