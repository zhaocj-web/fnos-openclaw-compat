#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import compat


class PatchTests(unittest.TestCase):
    def test_multiple_hunks_and_insertions(self):
        original = 'first\nsecond\nthird\nfourth\nfifth\n'
        delta = ('--- a\n+++ b\n@@ -1,2 +1,3 @@\n first\n-second\n+changed\n+inserted\n'
                 '@@ -4,2 +5,1 @@\n fourth\n-fifth\n')
        self.assertEqual(compat.apply_patch(original, delta), 'first\nchanged\ninserted\nthird\nfourth\n')

    def test_diverged_source_is_rejected(self):
        with self.assertRaises(ValueError):
            compat.apply_patch('modified\n', '--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n')

    def test_wrong_hunk_length_is_rejected(self):
        with self.assertRaises(ValueError):
            compat.apply_patch('old\n', '--- a\n+++ b\n@@ -1,2 +1 @@\n-old\n+new\n')

    def test_trailing_lines_preserved(self):
        self.assertEqual(compat.apply_patch('a\nb\n', '--- a\n+++ b\n@@ -1 +1 @@\n-a\n+c\n'), 'c\nb\n')


class RuntimeTests(unittest.TestCase):
    def test_existing_system_node_avoids_network_download(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / 'runtime'
            with patch.object(compat, 'RUNTIME', runtime), patch.object(compat, 'NODE', runtime / 'node'), \
                    patch.object(compat.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='v24.20.0\n')), \
                    patch.object(compat.shutil, 'copyfile', side_effect=lambda src, dst: Path(dst).write_bytes(b'fixture')), \
                    patch.object(compat.os, 'chown'), \
                    patch.object(compat.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000, pw_gid=1000)), \
                    patch.object(compat.Path, 'is_file', return_value=True), \
                    patch.object(compat.urllib.request, 'urlretrieve') as download:
                compat.node_runtime()
                self.assertEqual((runtime / 'node').read_bytes(), b'fixture')
                download.assert_not_called()


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.server = self.root / 'server.js'
        self.server.write_text('fixture server\n')
        self.hash = compat.digest(self.server.read_bytes())
        self.state = {'core': '2026.10.5', 'serverHash': self.hash, 'running': True, 'env': {}}
        self.overrides = [patch.object(compat, 'SERVER', self.server),
                          patch.dict(compat.MANIFEST, {'patchedServerSha256': self.hash}),
                          patch.object(compat, 'inspect', return_value=self.state)]
        for override in self.overrides:
            override.start()
            self.addCleanup(override.stop)

    def test_incremental_patch_is_selected_for_known_previous_build(self):
        target = 'updated fixture server\n'
        (self.root / 'delta.patch').write_text('--- a\n+++ b\n@@ -1 +1 @@\n-fixture server\n+updated fixture server\n')
        with patch.object(compat, 'HERE', self.root), \
                patch.dict(compat.MANIFEST, {'patchedServerSha256': compat.digest(target.encode()),
                                          'upgradePatches': {self.hash: 'delta.patch'}}), \
                patch.object(compat, 'action') as action:
            compat.install(dry_run=True)
            action.assert_not_called()

    def test_dry_run_never_stops_or_writes(self):
        with patch.object(compat, 'action') as action, patch.object(compat, 'snapshot') as backup:
            compat.install(dry_run=True)
            action.assert_not_called()
            backup.assert_not_called()
            self.assertEqual(self.server.read_text(), 'fixture server\n')

    def test_completed_backup_is_restored_after_install_failure(self):
        backup = self.root / 'backup'
        backup.mkdir()
        (backup / 'backup-complete').write_text('1')
        with patch.object(compat, 'action') as action, patch.object(compat, 'stop_monitor'), \
                patch.object(compat, 'snapshot', return_value=backup), \
                patch.object(compat, 'node_runtime', side_effect=RuntimeError('simulated download failure')), \
                patch.object(compat, 'restore') as restore:
            with self.assertRaisesRegex(RuntimeError, 'simulated download failure'):
                compat.install()
            restore.assert_called_once_with(backup)
            action.assert_called_once_with('stop')

    def test_snapshot_failure_restarts_original_service(self):
        with patch.object(compat, 'action') as action, patch.object(compat, 'stop_monitor'), \
                patch.object(compat, 'snapshot', side_effect=RuntimeError('no space')), \
                patch.object(compat, 'start_monitor') as start, patch.object(compat, 'restore') as restore:
            with self.assertRaisesRegex(RuntimeError, 'no space'):
                compat.install()
            start.assert_called_once_with({})
            self.assertEqual([x.args[0] for x in action.call_args_list], ['stop', 'start'])
            restore.assert_not_called()

    def test_unknown_source_stops_before_service_mutations(self):
        with patch.object(compat, 'inspect', side_effect=RuntimeError('unknown build')), \
                patch.object(compat, 'action') as action:
            with self.assertRaisesRegex(RuntimeError, 'unknown build'):
                compat.install()
            action.assert_not_called()


if __name__ == '__main__':
    unittest.main()
