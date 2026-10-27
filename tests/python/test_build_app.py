from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest
from unittest.mock import patch

from desktop_companion.build_app import BUNDLE_ID, install_bundle


def make_bundle(path, version, identifier=BUNDLE_ID):
    contents = path / 'Contents'
    contents.mkdir(parents=True)
    (contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': identifier}))
    (contents / 'version').write_text(version)
    return path


def version(path):
    return (path / 'Contents' / 'version').read_text()


class NativeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stage = make_bundle(self.root / 'source' / 'Desktop Companion.app', 'new')
        self.target = self.root / 'Applications' / 'Desktop Companion.app'
        self.backup = self.target.parent / '.Desktop Companion.previous'

    def test_first_install_keeps_build_source_and_cleans_staging(self):
        self.assertIsNone(install_bundle(self.stage, self.target))
        self.assertEqual(version(self.target), 'new')
        self.assertEqual(version(self.stage), 'new')
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])

    def test_upgrade_keeps_previous_app_beside_destination(self):
        make_bundle(self.target, 'old')
        make_bundle(self.backup, 'older')
        self.assertEqual(install_bundle(self.stage, self.target), self.backup)
        self.assertEqual(version(self.target), 'new')
        self.assertEqual(version(self.backup), 'old')

    def test_partial_copy_does_not_touch_installed_app_or_its_backup(self):
        make_bundle(self.target, 'old')
        make_bundle(self.backup, 'older')

        def failed_copy(source, destination, **kwargs):
            destination.mkdir()
            (destination / 'partial').write_text('incomplete')
            raise OSError('disk full')

        with patch('desktop_companion.build_app.shutil.copytree', side_effect=failed_copy):
            with self.assertRaisesRegex(OSError, 'disk full'):
                install_bundle(self.stage, self.target)
        self.assertEqual(version(self.target), 'old')
        self.assertEqual(version(self.backup), 'older')
        self.assertFalse(list(self.target.parent.glob('.desktop-companion-install-*')))

    def test_failed_activation_restores_working_app(self):
        make_bundle(self.target, 'old')
        original = Path.rename

        def fail_activation(source, destination):
            if source.parent.name.startswith('.desktop-companion-install-'):
                raise OSError('activation failed')
            return original(source, destination)

        with patch.object(Path, 'rename', new=fail_activation):
            with self.assertRaisesRegex(OSError, 'activation failed'):
                install_bundle(self.stage, self.target)
        self.assertEqual(version(self.target), 'old')
        self.assertFalse(self.backup.exists())

    def test_failed_restore_preserves_backup_and_reports_its_location(self):
        make_bundle(self.target, 'old')
        original = Path.rename

        def fail_activation_and_restore(source, destination):
            if source == self.backup or source.parent.name.startswith('.desktop-companion-install-'):
                raise OSError('volume unavailable')
            return original(source, destination)

        with patch.object(Path, 'rename', new=fail_activation_and_restore):
            with self.assertRaisesRegex(RuntimeError, 'Restore the previous app from') as error:
                install_bundle(self.stage, self.target)
        self.assertIn(str(self.backup), str(error.exception))
        self.assertEqual(version(self.backup), 'old')

    def test_source_and_destination_can_be_on_different_volumes(self):
        make_bundle(self.target, 'old')
        original = Path.rename
        renames = []

        def same_volume_only(source, destination):
            destination = Path(destination)
            self.assertTrue(source.is_relative_to(self.target.parent))
            self.assertTrue(destination.is_relative_to(self.target.parent))
            renames.append((source, destination))
            return original(source, destination)

        with patch.object(Path, 'rename', new=same_volume_only):
            install_bundle(self.stage, self.target)
        self.assertEqual(len(renames), 2)
        self.assertEqual(version(self.stage), 'new')

    def test_unrelated_target_or_backup_is_left_untouched(self):
        for location in (self.target, self.backup):
            with self.subTest(location=location):
                make_bundle(location, 'unrelated', identifier='another.app')
                with self.assertRaisesRegex(ValueError, 'unrelated app'):
                    install_bundle(self.stage, self.target)
                self.assertEqual(version(location), 'unrelated')
                shutil.rmtree(location)

    def test_linked_target_or_backup_is_rejected_including_dangling_link(self):
        self.target.parent.mkdir()
        for location in (self.target, self.backup):
            for destination in (self.stage, self.root / 'missing.app'):
                with self.subTest(location=location, destination=destination):
                    location.symlink_to(destination, target_is_directory=True)
                    with self.assertRaisesRegex(ValueError, 'linked or unrelated'):
                        install_bundle(self.stage, self.target)
                    self.assertTrue(location.is_symlink())
                    location.unlink()

    def test_next_install_preserves_backup_left_by_interruption(self):
        make_bundle(self.backup, 'old')
        install_bundle(self.stage, self.target)
        self.assertEqual(version(self.target), 'new')
        self.assertEqual(version(self.backup), 'old')


if __name__ == '__main__':
    unittest.main()
