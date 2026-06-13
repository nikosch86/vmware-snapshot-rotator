#!/usr/bin/env python3
"""Rotate VMware ESXi / vSphere VM snapshots, keeping a fixed number of recent ones.

The module is split into pure decision logic (:func:`plan_rotation`,
:func:`flatten_snapshots`, :func:`resolve_snapshot_name`) and the thin vSphere
I/O wrappers that drive it, so the rotation policy can be unit-tested without a
live vCenter.
"""
from __future__ import annotations

import argparse
import atexit
import getpass
import logging
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime

import coloredlogs
from pyVim.connect import Disconnect, SmartConnect
from pyVim.task import WaitForTask
from pyVmomi import vim

logger = logging.getLogger(__name__)

DEFAULT_DESCRIPTION = "Automatic snapshot taken by snapshot rotator tool"


@dataclass
class SnapshotInfo:
    """A flattened view of a single snapshot in a VM's snapshot tree."""

    name: str
    description: str
    create_time: object  # datetime.datetime as returned by vSphere
    state: object
    ref: object = field(default=None, repr=False)  # underlying vim.vm.Snapshot


@dataclass
class RotationPlan:
    """What to do for a single VM: whether to create, and which to delete."""

    create: bool
    delete: list  # list[SnapshotInfo]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rotate VMware ESXi / vSphere VM snapshots."
    )
    parser.add_argument("-s", "--host", required=True, help="Remote host to connect to")
    parser.add_argument(
        "-o", "--port", type=int, default=443, help="Port to connect on (default: %(default)s)"
    )
    parser.add_argument(
        "-u", "--user", required=True, help="User name to use when connecting to host"
    )
    parser.add_argument("-p", "--password", help="Password to use when connecting to host")
    parser.add_argument("-t", "--tag", help="Comment to append to the name of new snapshots")
    parser.add_argument("-m", "--description", help="Description to use for new snapshots")
    parser.add_argument(
        "-k", "--keep", type=int, default=3,
        help="How many snapshots to keep (default: %(default)s)",
    )
    parser.add_argument(
        "--prune-only", action="store_true",
        help="Only prune old snapshots, do not create snapshots",
    )
    parser.add_argument("-n", "--dry-run", action="store_true", help="Dry run")
    parser.add_argument("--verbose", "-v", action="count", default=0)
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def configure_logging(verbosity: int) -> None:
    levels = [logging.WARNING, logging.INFO, logging.DEBUG]
    level = levels[min(len(levels) - 1, verbosity)]
    coloredlogs.install(level=level)


# --------------------------------------------------------------------------- #
# Pure decision logic (no vSphere I/O)
# --------------------------------------------------------------------------- #
def flatten_snapshots(snapshot_tree) -> list[SnapshotInfo]:
    """Recursively flatten a vSphere snapshot tree into a flat list.

    ``snapshot_tree`` is a sequence of ``vim.vm.SnapshotTree`` nodes (e.g.
    ``vm.snapshot.rootSnapshotList``). Order matches the original depth-first
    traversal.
    """
    result: list[SnapshotInfo] = []
    for node in snapshot_tree or []:
        result.append(
            SnapshotInfo(
                name=node.name,
                description=node.description,
                create_time=node.createTime,
                state=node.state,
                ref=node.snapshot,
            )
        )
        result.extend(flatten_snapshots(node.childSnapshotList))
    return result


def resolve_snapshot_name(existing_names, today: date, now: datetime) -> str:
    """Pick a name for a new snapshot, avoiding collisions with existing ones.

    Uses the ISO date; if a snapshot with that name already exists, falls back
    to a second-precision ISO timestamp.
    """
    name = today.isoformat()
    if name in set(existing_names):
        name = now.isoformat(timespec="seconds")
    return name


def plan_rotation(snapshots, keep: int, prune_only: bool) -> RotationPlan:
    """Decide whether to create a snapshot and which existing ones to delete.

    NOTE: this intentionally reproduces the *historical* selection behaviour so
    the refactor is behaviour-preserving. It has two known bugs that are fixed
    in a later change and pinned by the test-suite:

    * when ``count > keep`` it selects the oldest snapshot repeatedly instead of
      the N distinct oldest snapshots;
    * it assumes ``snapshots`` is already ordered oldest-first (tree order),
      rather than sorting by ``create_time``.
    """
    count = len(snapshots)
    create = not prune_only
    delete: list = []
    if count > keep:
        to_delete = count - (keep - 1)
        delete = [snapshots[0]] * to_delete  # historical bug: repeats the oldest
    elif count == keep and create:
        delete = [snapshots[0]]
    return RotationPlan(create=create, delete=delete)


# --------------------------------------------------------------------------- #
# vSphere I/O
# --------------------------------------------------------------------------- #
def connect(host: str, user: str, password: str, port: int):
    # NOTE: TLS verification is disabled here; this is hardened in a later change.
    context = None
    if hasattr(ssl, "_create_unverified_context"):
        context = ssl._create_unverified_context()
    return SmartConnect(host=host, user=user, pwd=password, port=int(port), sslContext=context)


def iter_vms(content):
    for child in content.rootFolder.childEntity:
        if hasattr(child, "vmFolder"):
            yield from child.vmFolder.childEntity


def log_vm_summary(vm) -> None:
    summary = vm.summary
    logger.info("Name       : %s", summary.config.name)
    logger.debug("Path       : %s", summary.config.vmPathName)
    logger.debug("Guest      : %s", summary.config.guestFullName)
    annotation = summary.config.annotation
    if annotation:
        logger.debug("Annotation : %s", annotation)
    logger.debug("State      : %s", summary.runtime.powerState)
    if summary.guest is not None and summary.guest.ipAddress:
        logger.debug("IP         : %s", summary.guest.ipAddress)
    if summary.runtime.question is not None:
        logger.debug("Question   : %s", summary.runtime.question.text)
    if summary.guest is not None and summary.guest.toolsRunningStatus == "guestToolsNotRunning":
        logger.debug("tools not running")


def create_snapshot(vm, snapshot_name: str, description: str, tag=None, dry_run=False) -> None:
    if tag:
        snapshot_name = f"{snapshot_name} {tag}"
    logger.debug(
        "creating snapshot of VM '%s' using name '%s'", vm.summary.config.name, snapshot_name
    )
    if dry_run:
        return
    try:
        WaitForTask(
            vm.CreateSnapshot_Task(
                name=snapshot_name, memory=False, quiesce=False, description=description
            )
        )
    except Exception as exc:  # noqa: BLE001 - log and continue with other VMs
        logger.error("error trying to create snapshot: %s", exc)


def get_snapshots_by_name_recursively(snapshot_tree, name: str) -> list:
    matches = []
    for node in snapshot_tree or []:
        if node.name == name:
            matches.append(node)
        else:
            matches.extend(get_snapshots_by_name_recursively(node.childSnapshotList, name))
    return matches


def delete_snapshot_by_name(root_snapshot_list, name: str, dry_run=False) -> None:
    logger.debug("deleting snapshot '%s'", name)
    matches = get_snapshots_by_name_recursively(root_snapshot_list, name)
    if dry_run:
        return
    try:
        WaitForTask(matches[0].snapshot.RemoveSnapshot_Task(False))
    except Exception as exc:  # noqa: BLE001 - log and continue with other deletions
        logger.error("error trying to delete snapshot '%s': %s", name, exc)


def rotate(content, args) -> tuple[int, int]:
    """Walk every VM, applying the rotation plan. Returns (created, deleted)."""
    created = 0
    deleted = 0
    deletion_queue = []  # deferred so all snapshots are taken quickly first
    description = args.description or DEFAULT_DESCRIPTION

    for vm in iter_vms(content):
        log_vm_summary(vm)
        snapshots = flatten_snapshots(vm.snapshot.rootSnapshotList) if vm.snapshot else []
        plan = plan_rotation(snapshots, args.keep, args.prune_only)

        if plan.create:
            name = resolve_snapshot_name([s.name for s in snapshots], date.today(), datetime.now())
            logger.info("%i snapshots found, creating a snapshot", len(snapshots))
            create_snapshot(vm, name, description, tag=args.tag, dry_run=args.dry_run)
            created += 1

        for snap in plan.delete:
            deletion_queue.append((vm.snapshot.rootSnapshotList, snap.name))
            deleted += 1

    for root_list, snap_name in deletion_queue:
        delete_snapshot_by_name(root_list, snap_name, dry_run=args.dry_run)

    return created, deleted


def main(argv=None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    password = args.password or getpass.getpass(
        prompt=f"Enter password for host {args.host} and user {args.user}: "
    )

    try:
        si = connect(args.host, args.user, password, args.port)
    except vim.fault.InvalidLogin:
        logger.error(
            "failed logging in to %s as user %s: invalid credentials", args.host, args.user
        )
        return 1

    if not si:
        logger.critical(
            "Could not connect to the specified host using specified username and password"
        )
        return 1

    atexit.register(Disconnect, si)
    content = si.RetrieveContent()

    created, deleted = rotate(content, args)
    print(f"done rotating snapshots, {created} created, {deleted} deleted")
    return 0


if __name__ == "__main__":
    main()
