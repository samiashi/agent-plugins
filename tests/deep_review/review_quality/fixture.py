"""Create a local synthetic repository for the review-quality evaluation."""

import argparse
import subprocess
from pathlib import Path

BASE = {
    "README.md": """# Import service

Both single and bulk imports accept stored records with a string `name`.
Older records may have leading and trailing spaces. Existing import paths
must keep accepting them. Queued imports only receive records from the
same validation function as the single import.

The production adapter provides an opaque enqueue token. Existing
deployments must continue to work after an upgrade. The adapter's source,
installed version and production configuration are unavailable in this clone.
""",
    "imports.py": """def validate(row):
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a name is required")
    return row


def normalize(row):
    return row["name"].strip()


def import_one(row):
    return normalize(validate(row))


def import_bulk(stored_rows):
    return [normalize(row) for row in stored_rows]


def queue_import(adapter, row):
    return adapter.enqueue(validate(row))
""",
}

HEAD = {
    "imports.py": BASE["imports.py"]
    .replace('return row["name"].strip()', 'return row["name"].strip().lower()')
    .replace(
        "return [normalize(row) for row in stored_rows]",
        'return [normalize(row) for row in stored_rows if row["name"] == row["name"].strip()]',
    )
    .replace("return adapter.enqueue(validate(row))", "return adapter.enqueue(validate(row)).strip()"),
}


def git(root, *args):
    return subprocess.check_output(["git", "-C", root, *args], text=True).strip()


def commit(root, files, title):
    for name, content in files.items():
        (root / name).write_text(content)
    git(root, "add", ".")
    git(root, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", title)
    return git(root, "rev-parse", "HEAD")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="path to a new fixture repository")
    root = parser.parse_args().path.resolve()
    root.mkdir(parents=True)
    git(root, "init", "--quiet", "--template=")
    git(root, "config", "user.name", "Fixture Author")
    git(root, "config", "user.email", "fixture@example.invalid")
    base = commit(root, BASE, "Document and implement import contracts")
    head = commit(root, HEAD, "Normalize imported names and enqueue tokens")
    print(f"{base}..{head}")


if __name__ == "__main__":
    main()
