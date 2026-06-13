"""Unit tests for the pure decision logic and CLI parsing.

These pin the *intended* rotation policy. Some cases fail against the
behaviour-preserving refactor and are made green by the bug-fix change:

* the oldest-N selection must return N *distinct* snapshots, not the oldest
  repeated;
* selection must be by ``create_time``, not by tree order.
"""
import types
from datetime import date, datetime

import pytest

from snapshot_rotator import (
    SnapshotInfo,
    flatten_snapshots,
    parse_args,
    plan_rotation,
    resolve_snapshot_name,
)


def snap(name: str, day: int) -> SnapshotInfo:
    return SnapshotInfo(
        name=name,
        description="",
        create_time=datetime(2026, 1, day),
        state="poweredOff",
        ref=name,
    )


def names(snaps):
    return [s.name for s in snaps]


# --------------------------------------------------------------------------- #
# plan_rotation
# --------------------------------------------------------------------------- #
def test_empty_creates_without_deleting():
    plan = plan_rotation([], keep=3, prune_only=False)
    assert plan.create is True
    assert plan.delete == []


def test_below_keep_creates_without_deleting():
    plan = plan_rotation([snap("a", 1), snap("b", 2)], keep=3, prune_only=False)
    assert plan.create is True
    assert plan.delete == []


def test_at_keep_creates_and_deletes_one_oldest():
    snaps = [snap("a", 1), snap("b", 2), snap("c", 3)]
    plan = plan_rotation(snaps, keep=3, prune_only=False)
    assert plan.create is True
    assert names(plan.delete) == ["a"]


def test_above_keep_deletes_distinct_oldest():
    snaps = [snap(f"s{i}", i) for i in range(1, 6)]  # s1..s5, keep=3
    plan = plan_rotation(snaps, keep=3, prune_only=False)
    assert plan.create is True
    # create one + delete three oldest distinct -> ends at keep (3)
    assert names(plan.delete) == ["s1", "s2", "s3"]
    assert len({id(s) for s in plan.delete}) == 3  # no repeated objects


def test_selection_is_by_create_time_not_list_order():
    # deliberately scrambled list order; oldest by create_time are c (day1), a (day2)
    snaps = [snap("a", 2), snap("b", 5), snap("c", 1), snap("d", 4), snap("e", 3)]
    plan = plan_rotation(snaps, keep=3, prune_only=False)
    assert names(plan.delete) == ["c", "a", "e"]  # three oldest by time


def test_prune_only_never_creates():
    plan = plan_rotation([snap("a", 1)], keep=3, prune_only=True)
    assert plan.create is False


def test_prune_only_at_or_below_keep_deletes_nothing():
    snaps = [snap("a", 1), snap("b", 2), snap("c", 3)]
    plan = plan_rotation(snaps, keep=3, prune_only=True)
    assert plan.delete == []


def test_prune_only_above_keep_deletes_excess_only():
    snaps = [snap(f"s{i}", i) for i in range(1, 6)]  # 5 snaps, keep=3
    plan = plan_rotation(snaps, keep=3, prune_only=True)
    assert plan.create is False
    # no new snapshot, so prune down to exactly keep -> delete 2 oldest
    assert names(plan.delete) == ["s1", "s2"]


# --------------------------------------------------------------------------- #
# flatten_snapshots
# --------------------------------------------------------------------------- #
def _node(name, children=None):
    return types.SimpleNamespace(
        name=name,
        description=f"desc-{name}",
        createTime=datetime(2026, 1, 1),
        state="poweredOff",
        snapshot=f"ref-{name}",
        childSnapshotList=children or [],
    )


def test_flatten_handles_empty_and_none():
    assert flatten_snapshots([]) == []
    assert flatten_snapshots(None) == []


def test_flatten_is_depth_first_and_captures_ref():
    tree = [_node("root", children=[_node("child")]), _node("sibling")]
    flat = flatten_snapshots(tree)
    assert names(flat) == ["root", "child", "sibling"]
    assert flat[0].ref == "ref-root"
    assert flat[0].description == "desc-root"


# --------------------------------------------------------------------------- #
# resolve_snapshot_name
# --------------------------------------------------------------------------- #
def test_resolve_name_uses_iso_date_when_free():
    name = resolve_snapshot_name([], date(2026, 6, 13), datetime(2026, 6, 13, 14, 30, 5))
    assert name == "2026-06-13"


def test_resolve_name_falls_back_to_timestamp_on_collision():
    name = resolve_snapshot_name(
        ["2026-06-13"], date(2026, 6, 13), datetime(2026, 6, 13, 14, 30, 5)
    )
    assert name == "2026-06-13T14:30:05"


# --------------------------------------------------------------------------- #
# CLI parsing
# --------------------------------------------------------------------------- #
def test_parse_args_defaults():
    args = parse_args(["-s", "host", "-u", "user"])
    assert args.host == "host"
    assert args.user == "user"
    assert args.port == 443
    assert args.keep == 3
    assert args.prune_only is False
    assert args.dry_run is False
    assert args.insecure is False  # TLS verification on by default
    assert args.verbose == 0


def test_parse_args_flags():
    args = parse_args(
        ["-s", "h", "-u", "u", "-k", "5", "--prune-only", "-n", "-v", "-v", "-t", "tag", "-m", "d"]
    )
    assert args.keep == 5
    assert args.prune_only is True
    assert args.dry_run is True
    assert args.verbose == 2
    assert args.tag == "tag"
    assert args.description == "d"


def test_parse_args_requires_host_and_user():
    with pytest.raises(SystemExit):
        parse_args([])
