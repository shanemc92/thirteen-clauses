#!/usr/bin/env python3
"""Recompute the SRI hashes for the CDN assets in index.html.

    python3 python/tools/sri.py            # check the hashes in index.html
    python3 python/tools/sri.py --print    # just print them

jsdelivr serves npm package files byte-for-byte, so the hash of the file
inside the published tarball is the hash of what the browser will fetch.
This pulls from registry.npmjs.org rather than the CDN on purpose: if the
CDN were the thing that had been tampered with, hashing its response would
cheerfully pin the tampered file.

Bump a version in index.html, run this, paste the new integrity values in.
"""

import base64
import hashlib
import io
import pathlib
import re
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
INDEX = ROOT / "index.html"

# (npm package, version, path inside the package, path as jsdelivr serves it)
ASSETS = [
    ("@xterm/xterm", "5.5.0", "package/lib/xterm.js", "lib/xterm.js"),
    ("@xterm/xterm", "5.5.0", "package/css/xterm.css", "css/xterm.css"),
    ("@xterm/addon-fit", "0.10.0", "package/lib/addon-fit.js", "lib/addon-fit.js"),
]


def tarball_url(package, version):
    name = package.split("/")[-1]
    return f"https://registry.npmjs.org/{package}/-/{name}-{version}.tgz"


def sri_for(package, version, member):
    with urllib.request.urlopen(tarball_url(package, version)) as response:
        blob = response.read()
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        handle = archive.extractfile(member)
        if handle is None:
            sys.exit(f"{member} not found in {package}@{version}")
        data = handle.read()
    digest = hashlib.sha384(data).digest()
    return "sha384-" + base64.b64encode(digest).decode("ascii"), len(data)


def main():
    just_print = "--print" in sys.argv
    html = INDEX.read_text(encoding="utf-8") if INDEX.exists() else ""
    problems = 0

    for package, version, member, served in ASSETS:
        url = f"https://cdn.jsdelivr.net/npm/{package}@{version}/{served}"
        integrity, size = sri_for(package, version, member)
        print(f"{served:22s} {size:>7,} bytes  {integrity}")
        if just_print or not html:
            continue
        if url not in html:
            print(f"  WARNING index.html does not reference {url}")
            problems += 1
            continue
        # The integrity attribute belongs to the tag that carries this URL.
        tag = re.search(r"<(?:script|link)\b[^>]*"
                        + re.escape(url) + r"[^>]*>", html, re.S)
        if not tag or integrity not in tag.group(0):
            print("  MISMATCH index.html does not carry this hash")
            problems += 1
        else:
            print("  ok")

    if problems:
        print(f"\n{problems} problem(s). Update index.html.")
        return 1
    if not just_print:
        print("\nindex.html is pinned to the published files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
