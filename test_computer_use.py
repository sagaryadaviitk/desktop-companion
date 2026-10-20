import json
from pathlib import Path
import plistlib
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from computer_use import computer_use_snapshot,locked_use_versions,update_locked_use


class ComputerUseTests(unittest.TestCase):
    def test_record_is_allowlisted_and_is_not_current_access_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'check.json';p.write_text(json.dumps({'state':'blocked','checked_at':time.time()-1000,'locked_use_installed':True,'lid_closed':True,'screen_locked':True,'secret':'MUST_NOT_EXPOSE'}))
            result=computer_use_snapshot(p);self.assertEqual(result['state'],'blocked');self.assertTrue(result['stale']);self.assertNotIn('MUST_NOT_EXPOSE',str(result))
            p.write_text('invalid');self.assertEqual(computer_use_snapshot(p)['state'],'not_tested')

    def test_plugin_versions_identify_an_old_installed_component(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);installed=root/'installed';bundled=root/'.codex/computer-use/Codex Computer Use.app/Contents/SharedSupport/Codex Computer Use Installer.app/Contents/Resources/CodexComputerUseAuthorizationPlugin.bundle'
            for path,build in [(installed,'1000633'),(bundled,'1001501')]:
                (path/'Contents').mkdir(parents=True);(path/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleVersion':build,'CFBundleShortVersionString':build}))
            result=locked_use_versions(root,installed);self.assertTrue(result['update_available'])
            self.assertEqual(result['installed_version'],'1000633');self.assertEqual(result['available_version'],'1001501')

    def test_update_is_native_only_and_verifies_openai_signature_before_install(self):
        calls=[]
        def runner(args,**kwargs):calls.append((args,kwargs));return SimpleNamespace(returncode=0)
        with patch('computer_use.locked_use_versions',return_value={'update_available':False}):
            self.assertTrue(update_locked_use('/test-home',runner)['ok'])
        self.assertEqual(calls[0][0][0],'/usr/bin/codesign');self.assertIn('2DC432GLL2',str(calls[0][0]))
        self.assertTrue(calls[0][0][calls[0][0].index('-R')+1].startswith('=anchor apple generic'))
        self.assertEqual(calls[1][0][-1],'install');self.assertNotIn('shell',calls[1][1])
        calls.clear()
        def invalid(args,**kwargs):calls.append(args);return SimpleNamespace(returncode=1)
        with self.assertRaises(RuntimeError):update_locked_use('/test-home',invalid)
        self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
