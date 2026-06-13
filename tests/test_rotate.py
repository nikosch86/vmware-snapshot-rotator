"""Integration tests for rotate() driving fake vSphere objects.

WaitForTask is patched out; the fakes record which snapshots are created and
removed so we can assert end-to-end selection without a live vCenter.
"""
import types
from datetime import datetime

import pytest

import snapshot_rotator


class FakeRef:
    """Stands in for vim.vm.Snapshot; records removals."""

    def __init__(self, name, removed):
        self.name = name
        self._removed = removed

    def RemoveSnapshot_Task(self, remove_children):  # noqa: N802 - vSphere API name
        self._removed.append(self.name)
        return ("removed", self.name)


def make_node(name, day, removed, children=None):
    return types.SimpleNamespace(
        name=name,
        description="",
        createTime=datetime(2026, 1, day),
        state="poweredOff",
        snapshot=FakeRef(name, removed),
        childSnapshotList=children or [],
    )


class FakeVM:
    def __init__(self, name, nodes, created):
        self._created = created
        self.summary = types.SimpleNamespace(
            config=types.SimpleNamespace(
                name=name, vmPathName=f"[ds] {name}", guestFullName="Linux", annotation=""
            ),
            runtime=types.SimpleNamespace(powerState="poweredOn", question=None),
            guest=types.SimpleNamespace(ipAddress=None, toolsRunningStatus="guestToolsRunning"),
        )
        self.snapshot = types.SimpleNamespace(rootSnapshotList=nodes) if nodes else None

    def CreateSnapshot_Task(self, name, memory, quiesce, description):  # noqa: N802
        self._created.append((self.summary.config.name, name))
        return ("created", name)


def make_content(vms):
    datacenter = types.SimpleNamespace(vmFolder=types.SimpleNamespace(childEntity=vms))
    return types.SimpleNamespace(rootFolder=types.SimpleNamespace(childEntity=[datacenter]))


def make_args(**overrides):
    base = dict(keep=3, prune_only=False, dry_run=False, tag=None, description=None)
    base.update(overrides)
    return types.SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(snapshot_rotator, "WaitForTask", lambda task: None)


def test_rotate_creates_everywhere_and_prunes_only_the_excess():
    removed, created = [], []
    over = FakeVM("over", [make_node(f"s{i}", i, removed) for i in range(1, 6)], created)  # 5
    under = FakeVM("under", [make_node("only", 1, removed)], created)  # 1
    content = make_content([over, under])

    n_created, n_deleted = snapshot_rotator.rotate(content, make_args(keep=3))

    assert n_created == 2  # both VMs get a fresh snapshot
    assert n_deleted == 3  # only the 3 oldest of `over`
    assert removed == ["s1", "s2", "s3"]  # distinct oldest, oldest first
    assert len(created) == 2


def test_rotate_prune_only_removes_excess_without_creating():
    removed, created = [], []
    over = FakeVM("over", [make_node(f"s{i}", i, removed) for i in range(1, 6)], created)  # 5
    content = make_content([over])

    n_created, n_deleted = snapshot_rotator.rotate(content, make_args(keep=3, prune_only=True))

    assert n_created == 0
    assert n_deleted == 2  # prune down to keep, no new snapshot
    assert removed == ["s1", "s2"]
    assert created == []


def test_rotate_dry_run_touches_nothing():
    removed, created = [], []
    over = FakeVM("over", [make_node(f"s{i}", i, removed) for i in range(1, 6)], created)
    content = make_content([over])

    n_created, n_deleted = snapshot_rotator.rotate(content, make_args(keep=3, dry_run=True))

    # counters still reflect the plan, but no vSphere mutations happen
    assert removed == []
    assert created == []
    assert n_created == 1
    assert n_deleted == 3
