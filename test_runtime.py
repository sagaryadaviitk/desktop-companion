import json
from pathlib import Path
import tempfile
import unittest
import socket
from unittest.mock import patch

from runtime import AccountMonitor, SharedRuntime, RuntimeUnavailable, usage_snapshot, model_catalog


class UsageTests(unittest.TestCase):
    def test_remaining_uses_actual_week_window_in_either_slot(self):
        value={'rateLimitsByLimitId':{'codex':{'primary':{'windowDurationMins':300,'usedPercent':91},'secondary':{'windowDurationMins':10080,'usedPercent':10,'resetsAt':1000}}}}
        result=usage_snapshot(value,100)
        self.assertEqual(result['weekly'][0]['remaining_percent'],90)
        self.assertEqual(result['weekly'][0]['resets_at'],1000)
        self.assertEqual(result['checked_at'],100)

    def test_missing_bad_or_nonweekly_data_does_not_invent_a_balance(self):
        for window in (None,{'windowDurationMins':300,'usedPercent':10},{'windowDurationMins':10080,'usedPercent':None},{'windowDurationMins':10080,'usedPercent':True},{'windowDurationMins':10080,'usedPercent':101}):
            self.assertFalse(usage_snapshot({'rateLimits':{'primary':window}},100)['available'])

    def test_bucket_map_preferred_and_secrets_are_excluded(self):
        raw={'accountId':'PRIVATE_ACCOUNT','rateLimits':{'primary':{'windowDurationMins':10080,'usedPercent':0}},'rateLimitsByLimitId':{'codex':{'primary':{'windowDurationMins':10080,'usedPercent':30},'credits':{'balance':'PRIVATE_BALANCE'}}}}
        result=usage_snapshot(raw,100)
        self.assertEqual(result['weekly'][0]['remaining_percent'],70)
        self.assertNotIn('PRIVATE',json.dumps(result))

    def test_multiple_weekly_buckets_and_legacy_fallback(self):
        value={'rateLimitsByLimitId':{key:{'limitName':key,'primary':{'windowDurationMins':10080,'usedPercent':used}} for key,used in [('codex',100),('other',0)]}}
        self.assertEqual([v['remaining_percent'] for v in usage_snapshot(value,100)['weekly']],[0,100])
        self.assertEqual(usage_snapshot({'rateLimits':value['rateLimitsByLimitId']['codex']},100)['weekly'][0]['remaining_percent'],0)

    def test_catalog_excludes_hidden_models_and_only_exposes_picker_fields(self):
        result=model_catalog({'data':[{'model':'test-model','displayName':'Test','supportedReasoningEfforts':[{'reasoningEffort':'high'}],'PRIVATE':'hidden'}, {'id':'hidden','hidden':True}]})
        self.assertEqual(len(result),1);self.assertEqual(result[0]['efforts'],['high']);self.assertNotIn('PRIVATE',json.dumps(result))

    def test_account_monitor_caches_refresh_and_marks_expired_reading_stale(self):
        now=[1000]
        monitor=AccountMonitor(executable='test-cli',clock=lambda:now[0])
        monitor.usage=usage_snapshot({'rateLimits':{'primary':{'windowDurationMins':10080,'usedPercent':10}}},1000)
        monitor.next_refresh=9999
        self.assertFalse(monitor.snapshot()['usage']['stale'])
        now[0]=1121
        self.assertTrue(monitor.snapshot()['usage']['stale'])

    def test_monitor_uses_only_read_only_account_and_catalog_methods(self):
        calls=[]
        class Client:
            def __init__(self,command):calls.append(command)
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def call(self,method,params):
                calls.append(method)
                return {'rateLimits':{'primary':{'windowDurationMins':10080,'usedPercent':12}}} if method.startswith('account/') else {'data':[]}
        monitor=AccountMonitor(executable='test-cli',client_factory=Client)
        monitor._refresh()
        self.assertEqual(calls,[['test-cli','app-server','--stdio'],'account/rateLimits/read','model/list'])
        self.assertEqual(monitor.usage['weekly'][0]['remaining_percent'],88)

    def test_failed_refresh_keeps_value_explicitly_stale(self):
        monitor=AccountMonitor(executable='test-cli',client_factory=lambda *_:(_ for _ in ()).throw(RuntimeUnavailable('unavailable')))
        monitor.usage=usage_snapshot({'rateLimits':{'primary':{'windowDurationMins':10080,'usedPercent':10}}},1000)
        monitor._refresh()
        self.assertEqual(monitor.usage['weekly'][0]['remaining_percent'],90);self.assertTrue(monitor.usage['stale'])


class RuntimeTests(unittest.TestCase):
    def test_missing_shared_socket_cannot_start_another_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=SharedRuntime(temp,executable='test-cli')
            self.assertFalse(runtime.capabilities('test')['steer'])
            with self.assertRaises(RuntimeUnavailable):runtime.steer('test','hello','turn')
            with self.assertRaises(RuntimeUnavailable):runtime.set_model('test','model')

    def test_shared_control_socket_rejects_symlinks_and_writable_parent_or_endpoint(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as temp:
            runtime=SharedRuntime(temp,executable='test-cli');runtime.socket.parent.mkdir(mode=0o700)
            with socket.socket(socket.AF_UNIX) as endpoint:
                endpoint.bind(str(runtime.socket));runtime.socket.chmod(0o600)
                self.assertTrue(runtime.present())
                runtime.socket.chmod(0o666);self.assertFalse(runtime.present());runtime.socket.chmod(0o600)
                runtime.socket.parent.chmod(0o777);self.assertFalse(runtime.present());runtime.socket.parent.chmod(0o700)
                actual=runtime.socket.parent/'actual.sock';runtime.socket.rename(actual);runtime.socket.symlink_to(actual)
                self.assertFalse(runtime.present())

    def client(self,loaded=True,active=True,ack='turn-1'):
        calls=[]
        class Client:
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def call(self,method,params):
                calls.append((method,params))
                if method=='thread/loaded/list':return {'data':['chat-1'] if loaded else []}
                if method=='thread/read':return {'thread':{'canAcceptDirectInput':True,'status':{'type':'active' if active else 'idle'},'model':'model-a','reasoningEffort':'high'}}
                if method=='turn/steer':return {'turnId':ack}
                return {}
        return Client(),calls

    def test_steering_requires_existing_loaded_chat_and_matching_acknowledgement(self):
        runtime=SharedRuntime('/unused');client,calls=self.client()
        with patch.object(runtime,'client',return_value=client):result=runtime.steer('chat-1','correction','turn-1')
        self.assertEqual(result['state'],'steered')
        self.assertEqual(calls[-1],('turn/steer',{'threadId':'chat-1','expectedTurnId':'turn-1','input':[{'type':'text','text':'correction'}]}))
        self.assertFalse(any(method in ('thread/start','thread/resume','turn/start') for method,_ in calls))
        client,_=self.client(ack='another-turn')
        with patch.object(runtime,'client',return_value=client),self.assertRaises(RuntimeUnavailable):runtime.steer('chat-1','correction','turn-1')

    def test_unloaded_chat_is_never_resumed(self):
        runtime=SharedRuntime('/unused');client,calls=self.client(loaded=False)
        with patch.object(runtime,'client',return_value=client),self.assertRaises(RuntimeUnavailable):runtime.steer('chat-1','correction','turn-1')
        self.assertEqual([method for method,_ in calls],['thread/loaded/list'])

    def test_model_change_only_touches_model_and_effort_and_verifies_readback(self):
        runtime=SharedRuntime('/unused');client,calls=self.client()
        with patch.object(runtime,'client',return_value=client):result=runtime.set_model('chat-1','model-a','high')
        self.assertEqual(result['state'],'updated')
        self.assertIn(('thread/settings/update',{'threadId':'chat-1','model':'model-a','effort':'high'}),calls)
        with patch.object(runtime,'client',return_value=client),self.assertRaises(RuntimeUnavailable):runtime.set_model('chat-1','model-b','high')
