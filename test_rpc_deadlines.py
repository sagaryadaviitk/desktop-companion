"""Real pipe backpressure tests against disposable, synthetic app-server children."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from companion import CommandJournal
import runtime
from runtime import RpcClient, RuntimeUnavailable
from unittest.mock import patch


PREFIX = """import json,sys,time
request=json.loads(sys.stdin.readline())
print(json.dumps({'id':request['id'],'result':{}}),flush=True)
"""


class RpcDeadlineTests(unittest.TestCase):
    def client(self, body):
        client = RpcClient([sys.executable, '-u', '-c', PREFIX + body], timeout=3)
        self.addCleanup(client.close)
        return client

    def test_stalled_unicode_write_times_out_and_releases_command_journal(self):
        client = self.client('time.sleep(10)\n')
        client.timeout = 0.2
        errors = []
        with tempfile.TemporaryDirectory() as directory:
            journal = CommandJournal(Path(directory)/'commands.sqlite')
            def send():
                try: journal.execute('fixture-send', {}, lambda:client.call('fixture', {'text':'😀'*8000}))
                except Exception as error: errors.append(error)
            worker = threading.Thread(target=send, daemon=True)
            worker.start();worker.join(timeout=1)
            blocked = worker.is_alive()
            if blocked:
                client.process.terminate();worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(blocked, 'stdin write outlived its timeout and held the command journal')
            self.assertIsInstance(errors[0], RuntimeUnavailable)
            self.assertEqual(journal.lookup('fixture-send')['state'], 'unknown')
            self.assertIsNone(journal.lookup('another-command'))

    def test_large_unicode_request_is_written_completely_and_reply_is_matched(self):
        client = self.client("""json.loads(sys.stdin.readline())
request=json.loads(sys.stdin.readline())
print(json.dumps({'id':request['id'],'result':{'length':len(request['params']['text'])}}),flush=True)
""")
        self.assertEqual(client.call('fixture', {'text':'😀'*8000}), {'length':8000})

    def test_write_and_read_share_one_deadline(self):
        client = self.client("""time.sleep(.2)
json.loads(sys.stdin.readline())
request=json.loads(sys.stdin.readline())
time.sleep(.2)
print(json.dumps({'id':request['id'],'result':{}}),flush=True)
""")
        client.timeout = 0.3
        with self.assertRaises(RuntimeUnavailable): client.call('fixture', {'text':'😀'*8000})

    def test_notifications_are_drained_while_writing_to_avoid_duplex_deadlock(self):
        client = self.client("""print(json.dumps({'method':'fixture/notice','params':{'text':'x'*200000}}),flush=True)
json.loads(sys.stdin.readline())
request=json.loads(sys.stdin.readline())
print(json.dumps({'id':request['id'],'result':{'ok':True}}),flush=True)
""")
        self.assertEqual(client.call('fixture', {'text':'😀'*8000}), {'ok':True})

    def test_child_exit_and_protocol_errors_are_bounded_and_redacted(self):
        for body in ["json.loads(sys.stdin.readline());sys.stdin.close();sys.exit(0)\n", "json.loads(sys.stdin.readline());request=json.loads(sys.stdin.readline());print(json.dumps({'id':request['id'],'error':{'message':'PRIVATE_DETAIL'}}),flush=True)\n", "json.loads(sys.stdin.readline());sys.stdin.readline();print('[]',flush=True)\n"]:
            with self.subTest(body=body):
                client = self.client(body)
                with self.assertRaises(RuntimeUnavailable) as raised: client.call('fixture', {})
                self.assertNotIn('PRIVATE_DETAIL', str(raised.exception))

    def test_temporary_pipe_backpressure_retries_without_dropping_bytes(self):
        client=self.client("""json.loads(sys.stdin.readline())
request=json.loads(sys.stdin.readline())
print(json.dumps({'id':request['id'],'result':{'length':len(request['params']['text'])}}),flush=True)
""")
        original=runtime.os.write;calls=[]
        def write(fd,data):
            calls.append(1)
            if len(calls)==1:raise BlockingIOError()
            return original(fd,data)
        with patch('runtime.os.write',side_effect=write):
            self.assertEqual(client.call('fixture',{'text':'😀'*8000}),{'length':8000})
        self.assertGreater(len(calls),1)

    def test_zero_deadline_and_closed_or_failed_pipe_are_rejected(self):
        for mode in ['deadline','closed','zero-write']:
            with self.subTest(mode=mode):
                client=self.client('time.sleep(10)\n')
                if mode=='deadline':client.timeout=0
                if mode=='closed':client.process.stdin.close()
                with patch('runtime.os.write',return_value=0) if mode=='zero-write' else patch('runtime.os.write',wraps=runtime.os.write):
                    with self.assertRaises(RuntimeUnavailable):client.call('fixture',{})

    def test_closed_response_pipe_uses_fixed_disconnect_diagnostic(self):
        client=self.client('time.sleep(10)\n')
        client.process.stdout.close()
        with self.assertRaisesRegex(RuntimeUnavailable,'disconnected'):client._read_chunk()

    def test_oversized_response_is_rejected_before_unbounded_buffer_growth(self):
        client=self.client("""json.loads(sys.stdin.readline())
request=json.loads(sys.stdin.readline())
print(json.dumps({'id':request['id'],'result':{'text':'x'*(9*1024*1024)}}),flush=True)
""")
        with self.assertRaisesRegex(RuntimeUnavailable,'safe limit'):client.call('fixture',{})


if __name__ == '__main__': unittest.main()
