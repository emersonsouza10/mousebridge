"""Planejador de sincronização (função pura): cobre todos os ramos."""

import unittest

from foshar.foshar_sync.engine import Action, plan_sync


def kinds(local, remote, base):
    return {(a.kind, a.path) for a in plan_sync(local, remote, base)}


class PlanSyncTest(unittest.TestCase):
    def test_in_sync_no_actions(self) -> None:
        self.assertEqual(plan_sync({"a": "h"}, {"a": "h"}, {"a": "h"}), [])

    def test_remote_only_new_pulls(self) -> None:
        self.assertEqual(kinds({}, {"a": "h"}, {}), {("pull", "a")})

    def test_local_only_new_pushes(self) -> None:
        self.assertEqual(kinds({"a": "h"}, {}, {}), {("push", "a")})

    def test_local_edit_pushes(self) -> None:
        self.assertEqual(kinds({"a": "new"}, {"a": "old"}, {"a": "old"}), {("push", "a")})

    def test_remote_edit_pulls(self) -> None:
        self.assertEqual(kinds({"a": "old"}, {"a": "new"}, {"a": "old"}), {("pull", "a")})

    def test_local_delete_removes_remote(self) -> None:
        self.assertEqual(kinds({}, {"a": "old"}, {"a": "old"}), {("del_remote", "a")})

    def test_remote_delete_removes_local(self) -> None:
        self.assertEqual(kinds({"a": "old"}, {}, {"a": "old"}), {("del_local", "a")})

    def test_both_edited_conflict(self) -> None:
        self.assertEqual(kinds({"a": "L"}, {"a": "R"}, {"a": "old"}), {("conflict", "a")})

    def test_both_created_differently_conflict(self) -> None:
        self.assertEqual(kinds({"a": "L"}, {"a": "R"}, {}), {("conflict", "a")})

    def test_both_edited_to_same_sets_base(self) -> None:
        self.assertEqual(kinds({"a": "same"}, {"a": "same"}, {"a": "old"}), {("set_base", "a")})

    def test_both_deleted_cleans_index(self) -> None:
        self.assertEqual(kinds({}, {}, {"a": "old"}), {("del_index", "a")})

    def test_mixed_tree(self) -> None:
        local = {"keep": "h", "edit": "L2", "addlocal": "x"}
        remote = {"keep": "h", "edit": "L1", "addremote": "y"}
        base = {"keep": "h", "edit": "L1"}
        self.assertEqual(
            kinds(local, remote, base),
            {("push", "edit"), ("push", "addlocal"), ("pull", "addremote")},
        )


if __name__ == "__main__":
    unittest.main()
