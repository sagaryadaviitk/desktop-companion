from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import subprocess
from configuration import load,save,validate
from manage import bundle,ROOT,native_build_tools,doctor

class ConfigurationTests(unittest.TestCase):
    def test_clean_mac_defaults_to_local_and_preserves_explicit_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.assertFalse(load(root).remote_enabled)
            config={'hostname':'agents.example.com','tunnel_id':'12345678-1234-1234-1234-123456789abc','desktop_app':'/Applications/Example.app'}
            save(config,root);self.assertTrue(load(root).remote_enabled);self.assertEqual(str(load(root).app_path()),config['desktop_app'])
            self.assertEqual((root/'config.local.json').stat().st_mode&0o777,0o600)
    def test_incomplete_remote_credential_fields_and_unsafe_paths_are_rejected(self):
        for value in [{'hostname':'agents.example.com'},{'token':'SECRET'},{'hostname':'evil.test/path','tunnel_id':'12345678-1234-1234-1234-123456789abc'},{'desktop_app':'relative.app'}]:
            with self.assertRaises(ValueError):validate(value)
    def test_config_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'other').write_text('{}');(root/'config.local.json').symlink_to(root/'other')
            with self.assertRaises(ValueError):load(root)
    def test_export_is_source_only_and_starts_with_no_machine_access(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'export.zip';count=bundle(path)
            with zipfile.ZipFile(path) as z:
                names=z.namelist();self.assertEqual(count,len(names))
                self.assertIn('desktop_companion/config.example.json',names)
                self.assertIn('desktop_companion/web/attachments.js',names)
                for name in ['new_chat.js','appearance.js','status.js','theme.css']:self.assertIn('desktop_companion/web/'+name,names)
                self.assertIn('desktop_companion/system_status.py',names)
                for name in ('.gitattributes','AGENTS.md','CONTRIBUTING.md','.github/workflows/checks.yml','web/queue.js','web/conversation.js','test_conversation.cjs'):
                    self.assertIn('desktop_companion/'+name,names)
                for name in ['worker.mjs.template','offline.html','build.py']:self.assertIn('desktop_companion/cloudflare_fallback/'+name,names)
                self.assertFalse(any('/.state/' in n or n.endswith('config.local.json') or 'slack' in n or n.endswith('.app') for n in names))
                z.extractall(Path(temp)/'extracted')
            self.assertFalse(load(Path(temp)/'extracted/desktop_companion').remote_enabled)

    def test_native_tools_check_rejects_xcrun_stub_without_usable_compiler(self):
        with patch('manage.shutil.which',return_value='/usr/bin/xcrun'),patch('manage.subprocess.run',side_effect=[
            subprocess.CompletedProcess([],1,stdout='',stderr='invalid developer path'),
            subprocess.CompletedProcess([],1,stdout='',stderr='SDK not found')
        ]):
            ready,detail=native_build_tools()
        self.assertFalse(ready);self.assertIn('command-line tools',detail)

    def test_native_tools_check_requires_a_real_macos_sdk(self):
        for sdk in ('/nonexistent/DesktopCompanionTestSDK','','.'):
            with self.subTest(sdk=sdk),patch('manage.shutil.which',return_value='/usr/bin/xcrun'),patch('manage.subprocess.run',side_effect=[
                subprocess.CompletedProcess([],0,stdout='Apple Swift version',stderr=''),
                subprocess.CompletedProcess([],0,stdout=sdk,stderr='')
            ]):
                self.assertFalse(native_build_tools()[0])

    def test_native_tools_check_accepts_working_compiler_and_sdk(self):
        with tempfile.TemporaryDirectory() as sdk,patch('manage.shutil.which',return_value='/usr/bin/xcrun'),patch('manage.subprocess.run',side_effect=[
            subprocess.CompletedProcess([],0,stdout='Apple Swift version',stderr=''),
            subprocess.CompletedProcess([],0,stdout=sdk+'\n',stderr='')
        ]) as run:
            self.assertTrue(native_build_tools()[0])
            self.assertTrue(all(call.kwargs['timeout']==10 for call in run.call_args_list))

    def test_native_tools_check_handles_timeout_and_missing_launcher(self):
        with patch('manage.shutil.which',return_value=None),patch('manage.subprocess.run') as run:
            self.assertFalse(native_build_tools()[0]);run.assert_not_called()
        with patch('manage.shutil.which',return_value='/usr/bin/xcrun'),patch('manage.subprocess.run',side_effect=subprocess.TimeoutExpired('xcrun',10)):
            self.assertFalse(native_build_tools()[0])

    def test_browser_only_setup_does_not_invoke_native_build_tools(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'CODEX_HOME':temp}),patch('manage.native_build_tools') as native:
            checks=doctor(Path(temp),check_native=False)
            native.assert_not_called()
        native_check=next(check for check in checks if check['name']=='Native build tools')
        self.assertTrue(native_check['ok']);self.assertIn('Skipped',native_check['detail'])

if __name__=='__main__':unittest.main()
