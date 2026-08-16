#!/usr/bin/env python3
"""Generate a static Kodi add-on repository under repo/.

Kodi keeps add-ons up to date by polling a *repository add-on*, which points at
three kinds of static file: an ``addons.xml`` index, its md5 checksum, and one
zip per add-on version. Nothing dynamic is involved, so repo/ can be served by
GitHub Pages, by a web server on the LAN, or off a NAS share.

    python3 dev/tools/build_repo.py --url https://ozankiratli.github.io/kodi-fcast-receiver

The generated repository add-on is itself listed in the index, so once it is
installed on a device it can update itself along with everything else.
"""

import argparse
import hashlib
import os
import shutil
import sys
import xml.etree.ElementTree as ElementTree
import zipfile

# The repository root, two levels above dev/tools.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Identity of the repository add-on this script generates.
REPO_ID = "repository.fcast.ozankiratli"
REPO_NAME = "FCast Receiver Repository"
REPO_VERSION = "1.0.0"
REPO_PROVIDER = "ozankiratli"
REPO_SUMMARY = "Add-on repository for the FCast receiver"
REPO_DESCRIPTION = (
    "Delivers updates for the unofficial FCast receiver add-on for Kodi. "
    "Install this once and Kodi will keep the receiver up to date on its own."
)

# Kodi's minimum supported version for the repository add-on itself.
XBMC_PYTHON_VERSION = "3.0.0"


def read_addon_xml(path):
    """Return (id, version, <addon> element) from an addon.xml."""
    element = ElementTree.parse(path).getroot()
    return element.get("id"), element.get("version"), element


def write_zip(source_dir, target_zip, arc_root):
    """Zip source_dir into target_zip, rooted at arc_root/ inside the archive."""
    os.makedirs(os.path.dirname(target_zip), exist_ok=True)
    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for dirpath, dirnames, filenames in os.walk(source_dir):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in sorted(filenames):
                absolute = os.path.join(dirpath, filename)
                relative = os.path.relpath(absolute, source_dir)
                archive.write(absolute, os.path.join(arc_root, relative))


def build_repository_addon(url, staging_dir):
    """Write the repository add-on's source tree and return its addon.xml root.

    The three URLs below are the whole contract with Kodi: where the index is,
    where its checksum is, and where to find <datadir>/<id>/<id>-<version>.zip.
    """
    addon = ElementTree.Element("addon", {
        "id": REPO_ID,
        "name": REPO_NAME,
        "version": REPO_VERSION,
        "provider-name": REPO_PROVIDER,
    })

    requires = ElementTree.SubElement(addon, "requires")
    ElementTree.SubElement(requires, "import", {
        "addon": "xbmc.python", "version": XBMC_PYTHON_VERSION,
    })

    extension = ElementTree.SubElement(addon, "extension", {
        "point": "xbmc.addon.repository", "name": REPO_NAME,
    })
    directory = ElementTree.SubElement(extension, "dir")
    ElementTree.SubElement(directory, "info", {"compressed": "false"}).text = \
        f"{url}/addons.xml"
    ElementTree.SubElement(directory, "checksum").text = f"{url}/addons.xml.md5"
    ElementTree.SubElement(directory, "datadir", {"zip": "true"}).text = f"{url}/"

    metadata = ElementTree.SubElement(addon, "extension", {"point": "xbmc.addon.metadata"})
    ElementTree.SubElement(metadata, "summary", {"lang": "en"}).text = REPO_SUMMARY
    ElementTree.SubElement(metadata, "description", {"lang": "en"}).text = REPO_DESCRIPTION
    ElementTree.SubElement(metadata, "platform").text = "all"
    assets = ElementTree.SubElement(metadata, "assets")
    ElementTree.SubElement(assets, "icon").text = "icon.png"

    source = os.path.join(staging_dir, REPO_ID)
    os.makedirs(source, exist_ok=True)
    ElementTree.indent(addon, space="    ")
    ElementTree.ElementTree(addon).write(
        os.path.join(source, "addon.xml"), encoding="UTF-8", xml_declaration=True
    )
    shutil.copy(os.path.join(ROOT, "icon.png"), os.path.join(source, "icon.png"))

    return source, addon


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True,
                        help="base URL the repo/ directory will be served from")
    parser.add_argument("--out", default=os.path.join(ROOT, "repo"),
                        help="output directory (default: repo/)")
    parser.add_argument("--dist", default=os.path.join(ROOT, "dist"),
                        help="directory holding the built add-on zip (default: dist/)")
    args = parser.parse_args()

    url = args.url.rstrip("/")
    out = args.out
    staging = os.path.join(out, ".staging")

    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(staging)

    entries = []

    # 1. The receiver add-on. Its zip is produced by `make`, so require it
    #    rather than duplicating the payload rules here.
    addon_id, version, element = read_addon_xml(os.path.join(ROOT, "addon.xml"))
    built_zip = os.path.join(args.dist, f"{addon_id}-{version}.zip")
    if not os.path.exists(built_zip):
        parser.error(f"{built_zip} not found -- run `make` first")

    addon_out = os.path.join(out, addon_id)
    os.makedirs(addon_out)
    shutil.copy(built_zip, os.path.join(addon_out, os.path.basename(built_zip)))
    shutil.copy(os.path.join(ROOT, "icon.png"), os.path.join(addon_out, "icon.png"))
    changelog = os.path.join(ROOT, "changelog.txt")
    if os.path.exists(changelog):
        shutil.copy(changelog, os.path.join(addon_out, f"changelog-{version}.txt"))
    entries.append(element)
    print(f"  {addon_id} {version}")

    # 2. The repository add-on, so it can update itself once installed.
    repo_source, repo_element = build_repository_addon(url, staging)
    repo_out = os.path.join(out, REPO_ID)
    os.makedirs(repo_out)
    write_zip(repo_source, os.path.join(repo_out, f"{REPO_ID}-{REPO_VERSION}.zip"), REPO_ID)
    shutil.copy(os.path.join(ROOT, "icon.png"), os.path.join(repo_out, "icon.png"))
    entries.append(repo_element)
    print(f"  {REPO_ID} {REPO_VERSION}")

    # 3. The index and its checksum. Kodi refetches addons.xml only when the
    #    md5 changes, so the two must always be written together.
    index = ElementTree.Element("addons")
    index.extend(entries)
    ElementTree.indent(index, space="    ")
    index_bytes = ElementTree.tostring(index, encoding="UTF-8", xml_declaration=True)

    with open(os.path.join(out, "addons.xml"), "wb") as handle:
        handle.write(index_bytes)
    with open(os.path.join(out, "addons.xml.md5"), "w") as handle:
        handle.write(hashlib.md5(index_bytes).hexdigest())

    shutil.rmtree(staging)

    print(f"\nrepository written to {out}/ for {url}")
    print(f"install this zip once per device, then Kodi self-updates:")
    print(f"  {url}/{REPO_ID}/{REPO_ID}-{REPO_VERSION}.zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
