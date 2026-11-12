"""Refuse to overwrite an existing GHCR release image; fail closed on registry errors."""

import base64
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main():
    image = os.environ["IMAGE"]
    version = os.environ["RELEASE_VERSION"]
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{image}:pull"})
    # Docker login does not configure urllib; use the workflow token for this probe.
    credentials = base64.b64encode(
        f"{os.environ['GITHUB_ACTOR']}:{os.environ['GHCR_TOKEN']}".encode()).decode()
    request = Request(f"https://ghcr.io/token?{query}", headers={"Authorization": f"Basic {credentials}"})
    with urlopen(request, timeout=20) as response:
        token = json.load(response)["token"]
    request = Request(
        f"https://ghcr.io/v2/{image}/manifests/{version}", method="HEAD",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json"},
    )
    try:
        with urlopen(request, timeout=20):
            pass
    except HTTPError as exc:
        exc.close()
        if exc.code == 404:
            print(f"New image version: {image}:{version}")
            return 0
        raise
    print(f"Refusing to overwrite existing release {image}:{version}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
