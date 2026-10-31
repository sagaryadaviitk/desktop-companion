from pathlib import Path
from contextlib import closing,redirect_stdout
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
import subprocess
from configuration import load,save,validate
from manage import bundle,ROOT,native_build_tools,doctor,history_schema,cli_queue_capabilities,main
from test_companion import make_history

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
                self.assertIn('desktop_companion/COMPATIBILITY.md',names)
                self.assertIn('desktop_companion/web/attachments.js',names)
                for name in ['new_chat.js','appearance.js','status.js','theme.css']:self.assertIn('desktop_companion/web/'+name,names)
                self.assertIn('desktop_companion/system_status.py',names)
                for name in ('.gitattributes','AGENTS.md','CONTRIBUTING.md','.github/workflows/checks.yml','native/ControlPolicies.swift','test_native_policies.py','approvals.py','web/approvals.js','test_approvals.py','test_approvals_ui.cjs','web/queue.js','web/conversation.js','test_conversation.cjs','web/shell.js','test_shell.cjs'):
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
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'CODEX_HOME':temp}),patch('manage.native_build_tools') as native,patch('manage.cli_queue_capabilities',return_value=(False,'No test CLI.')):
            checks=doctor(Path(temp),check_native=False)
            native.assert_not_called()
        native_check=next(check for check in checks if check['name']=='Native build tools')
        self.assertTrue(native_check['ok']);self.assertIn('Skipped',native_check['detail'])


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.home=self.root/'history';self.home.mkdir()
        self.history=make_history(self.home)
        self.app=self.root/'Fixture.app';self.app.mkdir()
        self.cli=self.root/'codex';self.cli.write_text('Fixture executable; never run by these tests.\n');self.cli.chmod(0o700)
        config=SimpleNamespace(app_path=lambda:self.app,codex_path=lambda:str(self.cli),remote_enabled=False)
        for target,value in [('manage.load',config),('manage.platform.system','Darwin'),('manage.platform.mac_ver',('13.0','',''))]:
            patcher=patch(target,return_value=value);patcher.start();self.addCleanup(patcher.stop)
        environment=patch.dict(os.environ,{'CODEX_HOME':str(self.home)})
        environment.start();self.addCleanup(environment.stop)
        runner=patch('manage.subprocess.run',return_value=subprocess.CompletedProcess([],0,stdout='queue --thread --message --image',stderr=''))
        self.runner=runner.start();self.addCleanup(runner.stop)

    def check(self,name):
        return next(check for check in doctor(self.root,check_native=False) if check['name']==name)

    def test_full_supported_fixture_passes_and_real_history_queries_work(self):
        self.assertTrue(all(check['ok'] for check in doctor(self.root,check_native=False)))
        chats=self.history.list_threads()
        self.assertTrue(chats)
        self.assertTrue(self.history.read_thread(chats[0]['id'])['messages'])
        self.assertTrue(self.history.list_projects(chats))
        self.runner.assert_called_once_with([str(self.cli),'queue','--help'],capture_output=True,text=True,timeout=3)

    def test_table_names_alone_fail_with_actionable_column_diagnostic(self):
        for name,tables in [('state_5.sqlite',('threads','projects','project_roots')),('thread_history_1.sqlite',('thread_items','thread_turns'))]:
            (self.home/name).unlink()
            with closing(sqlite3.connect(self.home/name)) as db,db:
                for table in tables:db.execute(f'CREATE TABLE {table}(id TEXT)')
        check=self.check('Local history')
        self.assertFalse(check['ok'])
        self.assertIn('threads is missing required columns',check['detail'])
        self.assertIn('COMPATIBILITY.md',check['detail'])
        self.assertNotIn(str(self.home),check['detail'])

    def test_required_columns_cover_catalog_history_and_new_chat_reads(self):
        # Representative fields beyond the original table-only check, including
        # metadata used by project discovery, history, activity and live receipts.
        for filename,table,column in [
            ('state_5.sqlite','threads','git_branch'),
            ('state_5.sqlite','projects','created_at_ms'),
            ('state_5.sqlite','project_roots','position'),
            ('thread_history_1.sqlite','thread_items','item_json'),
            ('thread_history_1.sqlite','thread_turns','rollout_ordinal'),
        ]:
            with self.subTest(table=table,column=column),tempfile.TemporaryDirectory() as temporary:
                home=Path(temporary);make_history(home)
                with closing(sqlite3.connect(home/filename)) as db,db:
                    columns=[row[1] for row in db.execute(f'PRAGMA table_info({table})') if row[1]!=column]
                    db.execute(f'ALTER TABLE {table} RENAME TO old_table')
                    db.execute(f'CREATE TABLE {table} AS SELECT {",".join(columns)} FROM old_table')
                    db.execute('DROP TABLE old_table')
                ok,detail=history_schema(home)
                self.assertFalse(ok);self.assertIn(column,detail)

    def test_optional_fields_and_extra_columns_preserve_supported_fallbacks(self):
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db,db:
            db.execute('CREATE TABLE thread_read_receipts(thread_id TEXT,first_unread_turn TEXT)')
            db.execute('ALTER TABLE threads ADD COLUMN unrelated_future_field TEXT')
        with closing(sqlite3.connect(self.home/'queue_1.sqlite')) as db,db:
            db.execute('CREATE TABLE queued_items(id TEXT,thread_id TEXT,payload_json TEXT,created_at_ms INTEGER,queue_order INTEGER)')
        self.assertTrue(self.check('Local history')['ok'])
        self.assertTrue(self.history.list_threads())

    def test_present_optional_receipts_and_queue_require_consumed_columns(self):
        for filename,statement,missing in [
            ('state_5.sqlite','CREATE TABLE thread_read_receipts(thread_id TEXT)','first_unread_turn'),
            ('queue_1.sqlite','CREATE TABLE queued_items(id TEXT,thread_id TEXT,payload_json TEXT,created_at_ms INTEGER)','queue_order'),
        ]:
            with self.subTest(filename=filename),tempfile.TemporaryDirectory() as temporary:
                home=Path(temporary);make_history(home)
                with closing(sqlite3.connect(home/filename)) as db,db:db.execute(statement)
                ok,detail=history_schema(home)
                self.assertFalse(ok);self.assertIn(missing,detail)

    def test_present_queue_database_requires_queue_table_and_views_are_rejected(self):
        with closing(sqlite3.connect(self.home/'queue_1.sqlite')) as db,db:db.execute('CREATE TABLE unrelated(id TEXT)')
        ok,detail=history_schema(self.home)
        self.assertFalse(ok);self.assertIn('missing required table queued_items',detail)
        (self.home/'queue_1.sqlite').unlink()
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db,db:
            db.execute('ALTER TABLE projects RENAME TO projects_storage')
            db.execute('CREATE VIEW projects AS SELECT * FROM projects_storage')
        ok,detail=history_schema(self.home)
        self.assertFalse(ok);self.assertIn('missing required table projects',detail)

    def test_metadata_checks_use_read_only_connections_and_leave_files_unchanged(self):
        before={path.name:hashlib.sha256(path.read_bytes()).digest() for path in self.home.iterdir()}
        connect=sqlite3.connect;connections=[];statements=[]
        def recording_connect(database_uri,**options):
            self.assertTrue(database_uri.endswith('?mode=ro'));self.assertTrue(options['uri']);self.assertEqual(options['timeout'],2)
            connection=connect(database_uri,**options)
            connection.set_trace_callback(statements.append)
            connections.append(database_uri)
            return connection
        with patch('manage.sqlite3.connect',side_effect=recording_connect):
            self.assertTrue(history_schema(self.home)[0])
        self.assertEqual(len(connections),2)
        self.assertEqual(statements.count('PRAGMA query_only=ON'),2)
        self.assertTrue(all(sql.startswith(('PRAGMA ','SELECT name FROM sqlite_master')) for sql in statements))
        after={path.name:hashlib.sha256(path.read_bytes()).digest() for path in self.home.iterdir()}
        self.assertEqual(before,after)
        self.assertFalse((self.home/'queue_1.sqlite').exists())
        with tempfile.TemporaryDirectory() as empty:
            self.assertFalse(history_schema(Path(empty))[0])
            self.assertEqual(list(Path(empty).iterdir()),[])

    def test_unreadable_metadata_errors_do_not_print_paths_or_database_contents(self):
        with patch('manage.sqlite3.connect',side_effect=sqlite3.DatabaseError('PRIVATE CONTENT '+str(self.home))):
            ok,detail=history_schema(self.home)
        self.assertFalse(ok);self.assertNotIn('PRIVATE CONTENT',detail);self.assertNotIn(str(self.home),detail)

    def test_macos_floor_and_unknown_versions(self):
        for system,version,expected in [('Darwin','12.7',False),('Darwin','13.0',True),('Darwin','26.0',True),('Darwin','',False),('Linux','13.0',False)]:
            with self.subTest(system=system,version=version),patch('manage.platform.system',return_value=system),patch('manage.platform.mac_ver',return_value=(version,'','')):
                self.assertEqual(self.check('macOS')['ok'],expected)

    def test_cli_nonexistent_and_nonexecutable_paths_are_not_invoked(self):
        for path in (None,str(self.root/'missing'),str(self.app)):
            with self.subTest(path=path):self.assertFalse(cli_queue_capabilities(path)[0])
        self.cli.chmod(0o600)
        self.assertFalse(cli_queue_capabilities(str(self.cli))[0]);self.runner.assert_not_called()

    def test_cli_help_failure_timeout_and_missing_flags_fail_without_output_leaks(self):
        for response in [
            subprocess.CompletedProcess([],1,stdout='PRIVATE CONTENT',stderr='PRIVATE CREDENTIAL'),
            subprocess.CompletedProcess([],0,stdout='--thread',stderr=''),
            subprocess.TimeoutExpired('queue',3,output='PRIVATE CONTENT'),
            OSError('PRIVATE PATH'),
        ]:
            with self.subTest(response=response):
                if isinstance(response,Exception):self.runner.side_effect=response
                else:self.runner.side_effect=None;self.runner.return_value=response
                ok,detail=cli_queue_capabilities(str(self.cli))
                self.assertFalse(ok);self.assertNotIn('PRIVATE',detail)

    def test_cli_without_photo_flag_preserves_text_queue_support_and_reports_limit(self):
        self.runner.return_value=subprocess.CompletedProcess([],0,stdout='--thread --message',stderr='')
        ok,detail=cli_queue_capabilities(str(self.cli))
        self.assertTrue(ok);self.assertIn('Photo queue RPC capability is checked on first use',detail)

    def test_cli_help_requires_exact_option_tokens_and_does_not_infer_photo_support(self):
        for output in ('--thread-id --message-file','--thread --message-file','--thread-id --message'):
            with self.subTest(output=output):
                self.runner.return_value=subprocess.CompletedProcess([],0,stdout=output,stderr='')
                self.assertFalse(cli_queue_capabilities(str(self.cli))[0])
        self.runner.return_value=subprocess.CompletedProcess([],0,stdout='--thread --message --image-size',stderr='')
        ok,detail=cli_queue_capabilities(str(self.cli))
        self.assertTrue(ok);self.assertIn('Photo queue RPC capability is checked on first use',detail)

    def test_cli_help_accepts_multiline_options_with_values(self):
        output='Usage: codex queue [OPTIONS]\n\nOptions:\n  --thread <THREAD_ID>\n  --message=<MESSAGE>\n  --image <PATH>\n'
        self.runner.return_value=subprocess.CompletedProcess([],0,stdout=output,stderr='')
        ok,detail=cli_queue_capabilities(str(self.cli))
        self.assertTrue(ok);self.assertIn('Photo queue RPC capability is checked on first use',detail)


class DoctorCommandTests(unittest.TestCase):
    def test_doctor_without_app_skips_native_preflight_and_never_prepares_or_installs(self):
        for options,check_native in [([],True),(['--without-app'],False)]:
            with self.subTest(options=options),patch.object(sys,'argv',['manage.py','doctor',*options]),patch('manage.doctor',return_value=[{'name':'Local history','ok':True,'detail':'Fixture preflight only.'}]) as preflight,patch('manage.load') as load_config,patch('manage.save') as save_config,patch('manage.subprocess.run') as runner,patch('manage.bundle') as export,redirect_stdout(io.StringIO()):
                main()
            preflight.assert_called_once_with(check_native=check_native)
            load_config.assert_not_called();save_config.assert_not_called()
            runner.assert_not_called();export.assert_not_called()

if __name__=='__main__':unittest.main()
