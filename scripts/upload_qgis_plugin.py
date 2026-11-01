#!/usr/bin/env python3
"""Upload a QGIS plugin ZIP to plugins.qgis.org through its XML-RPC endpoint.

This is the same ``plugin.upload`` call the QGIS Plugin Builder's
``plugin_upload.py`` performs, kept dependency-free so the release workflow
can run it on a bare ``ubuntu-latest`` runner.

Credentials come from the environment (an OSGeo user id, *not* a GitHub
account)::

    OSGEO_USERNAME=<osgeo-id> OSGEO_PASSWORD=<password> \\
        python scripts/upload_qgis_plugin.py dist/gispulse-qgis-plugin-2.5.2.zip

The first upload creates the plugin entry under the uploader's account; later
uploads add a version to it. Exit codes: 0 uploaded, 2 usage / missing
credentials, 1 the repository rejected the ZIP (the validator message is
printed verbatim).
"""

from __future__ import annotations

import os
import sys
import xmlrpc.client
from pathlib import Path
from urllib.parse import quote

ENDPOINT = "https://{user}:{password}@plugins.qgis.org:443/plugins/RPC2/"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: upload_qgis_plugin.py <plugin.zip>\n")
        return 2
    zip_path = Path(argv[1])
    if not zip_path.is_file():
        sys.stderr.write(f"not a file: {zip_path}\n")
        return 2

    user = os.environ.get("OSGEO_USERNAME", "")
    password = os.environ.get("OSGEO_PASSWORD", "")
    if not user or not password:
        sys.stderr.write("OSGEO_USERNAME and OSGEO_PASSWORD must be set.\n")
        return 2

    endpoint = ENDPOINT.format(user=quote(user, safe=""), password=quote(password, safe=""))
    server = xmlrpc.client.ServerProxy(endpoint, verbose=False)
    payload = xmlrpc.client.Binary(zip_path.read_bytes())
    try:
        plugin_id, version_id = server.plugin.upload(payload)
    except xmlrpc.client.Fault as exc:
        # Validation errors (missing LICENSE, bad metadata, …) come back as faults.
        sys.stderr.write(f"plugins.qgis.org rejected {zip_path.name}: {exc.faultString}\n")
        return 1
    except xmlrpc.client.ProtocolError as exc:
        sys.stderr.write(f"HTTP {exc.errcode} from plugins.qgis.org: {exc.errmsg}\n")
        return 1

    print(f"uploaded {zip_path.name}: plugin id {plugin_id}, version id {version_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
