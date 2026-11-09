#!/usr/bin/env python3
"""Add fleet-selected static deltas to an extracted update, leaving CAS intact."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen


def checksum(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"Invalid OSTree checksum: {value!r}")
    return value


def ostree(repo, *args):
    return subprocess.check_output(
        ["ostree", f"--repo={repo}", *map(str, args)], text=True
    ).strip()


def get_json(path):
    request = Request(
        os.environ["UPDATE_SERVER_URL"].rstrip("/") + path,
        headers={"Authorization": "Bearer " + os.environ["UPDATE_SERVER_TOKEN"]},
    )
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def source_commits(tag, machine, fetch=get_json):
    commits = set()
    for update in fetch("/v1/updates") or []:
        if update["tag"] != tag or update["device-count"] <= 0:
            continue
        name = update["name"]
        metadata = fetch(f"/v1/updates/{quote(name, safe='')}/tuf/targets.json")
        targets = metadata["signed"]["targets"]
        matched = False
        for target in targets.values():
            if machine not in target.get("custom", {}).get("hardwareIds", []):
                continue
            commits.add(checksum(target["hashes"]["sha256"]))
            matched = True
        if not matched:
            print(f"Skipping {name}: no target for hardware ID {machine}", flush=True)
    return commits


def archive_commit(archive, machine):
    # Old CAS entries predate the sidecar. Read only the machine ref rather
    # than unpacking the whole image. New entries avoid this gzip scan.
    sidecar = archive.parent / "ostree-commit"
    if sidecar.exists():
        return checksum(sidecar.read_text().strip())
    with tarfile.open(archive, "r|gz") as bundle:
        for member in bundle:
            if member.name.removeprefix("./") == f"ostree_repo/refs/heads/{machine}":
                if not member.isfile() or member.size > 65:
                    raise ValueError(f"Invalid machine ref in {archive}")
                return checksum(bundle.extractfile(member).read().decode().strip())
    raise ValueError(f"No ref for {machine} in {archive}")


def find_sources(cas, assembled, machine, wanted):
    sources = {}
    # Existing assembled updates provide a cheap index for legacy CAS entries.
    # Resolve their yocto symlink, but only accept this project's CAS paths.
    for update in sorted(assembled.glob("update_*")):
        ref = update / machine / "ostree_repo/refs/heads" / machine
        archive = (update / "yocto" / machine / "ostree_repo.tgz").resolve()
        if ref.is_file() and archive.is_file() and archive.is_relative_to(cas.resolve()):
            commit = checksum(ref.read_text().strip())
            if commit in wanted:
                sources.setdefault(commit, archive)
    archives = sorted(cas.glob(f"*/{machine}/ostree_repo.tgz"))
    # Prefer cheap sidecars; decompress legacy archives only if still needed.
    archives.sort(key=lambda p: not (p.parent / "ostree-commit").exists())
    for archive in archives:
        if wanted <= sources.keys():
            break
        commit = archive_commit(archive, machine)
        if commit in wanted:
            sources.setdefault(commit, archive)
    return sources


def generate(repo, machine, sources, work_dir=None):
    destination = checksum(ostree(repo, "rev-parse", machine))
    with tempfile.TemporaryDirectory(prefix="ostree-deltas-", dir=work_dir) as tmp:
        root = Path(tmp)
        scratch = root / "repo"
        ostree(scratch, "init", "--mode=archive-z2")
        ostree(scratch, "pull-local", repo.resolve(), destination)
        for commit, archive in sorted(sources.items()):
            if commit == destination:
                continue
            print(f"Generating delta {commit} -> {destination} from {archive}", flush=True)
            unpack = root / "source"
            unpack.mkdir()
            with tarfile.open(archive) as bundle:
                bundle.extractall(unpack, filter="data")
            source = unpack / "ostree_repo"
            if checksum(ostree(source, "rev-parse", machine)) != commit:
                raise ValueError(f"CAS mapping does not match extracted ref: {archive}")
            ostree(scratch, "pull-local", source, commit)
            ostree(scratch, "static-delta", "generate", f"--from={commit}", f"--to={destination}")
            shutil.rmtree(unpack)
        if (scratch / "deltas").exists():
            shutil.copytree(scratch / "deltas", repo / "deltas", dirs_exist_ok=True)
    ostree(repo, "summary", "--update")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--cas", required=True, type=Path, help="Project's CAS yocto directory")
    parser.add_argument("--assembled", required=True, type=Path)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--work-dir", type=Path, help="Scratch disk with room for source images")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.machine):
        parser.error("Invalid machine name")
    destination = checksum(ostree(args.repo, "rev-parse", args.machine))
    wanted = source_commits(args.tag, args.machine) - {destination}
    sources = find_sources(args.cas, args.assembled, args.machine, wanted)
    for missing in sorted(wanted - sources.keys()):
        print(f"::warning::No CAS artifact for {args.machine} commit {missing}; clients will use an object pull")
    if sources:
        generate(args.repo, args.machine, sources, args.work_dir)
    print(f"Prepared {len(sources)} static delta(s) for {args.tag}", flush=True)


if __name__ == "__main__":
    main()
