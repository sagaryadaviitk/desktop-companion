import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop_companion.system_status import TunnelReporter,publish,read_record,tunnel_snapshot,record_event


class TunnelStatusTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.state=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_connector_registration_is_not_proof_of_public_access(self):
        reporter=TunnelReporter(self.state)
        reporter.log('INF Registered tunnel connection connIndex=0 protocol=http2')
        value=tunnel_snapshot(self.state)
        self.assertEqual(value['connections'],1);self.assertFalse(value['public_ready'])
        reporter.checked(401);self.assertTrue(tunnel_snapshot(self.state)['public_ready'])
        reporter.log('Lost connection with the edge connIndex=0');reporter.checked(530)
        self.assertFalse(tunnel_snapshot(self.state)['public_ready'])

    def test_transport_failure_is_kept_without_leaking_raw_log_details(self):
        reporter=TunnelReporter(self.state)
        reporter.log('ERR read PRIVATE_IP:7844 connection reset by peer SECRET_TOKEN connIndex=0')
        reporter.checked(530);reporter.update('failed')
        value=tunnel_snapshot(self.state);self.assertIn('connection to Cloudflare was closed',value['detail'])
        for private in ('PRIVATE_IP','SECRET_TOKEN','controller_pid','connector_pid'):
            self.assertNotIn(private,json.dumps(value))
        self.assertEqual((self.state/'tunnel-status.json').stat().st_mode&0o777,0o600)

    def test_stale_or_missing_healthy_record_never_claims_current_connectivity(self):
        self.assertFalse(tunnel_snapshot(self.state)['public_ready'])
        with patch('desktop_companion.system_status.time.time',return_value=100):publish(self.state,'ready',public_http_status=401)
        value=tunnel_snapshot(self.state,now=200);self.assertTrue(value['stale']);self.assertFalse(value['public_ready'])
        self.assertFalse(tunnel_snapshot(self.state,now=99)['public_ready'])

    def test_retry_explains_automatic_recovery_and_local_only_is_explicit(self):
        with patch('desktop_companion.system_status.time.time',return_value=100):publish(self.state,'retrying',retry_at=130,reason='network_changed')
        value=tunnel_snapshot(self.state,now=110);self.assertEqual(value['retry_in_seconds'],20);self.assertIn('network changed',value['detail'])
        self.assertEqual(tunnel_snapshot(self.state,configured=False)['state'],'disabled')

    def test_authentication_bypass_is_never_ready(self):
        reporter=TunnelReporter(self.state);reporter.checked(200)
        value=tunnel_snapshot(self.state);self.assertFalse(value['public_ready']);self.assertIn('protect your chats',value['detail'])

    def test_status_reader_rejects_symlink_and_omits_unrecognized_values(self):
        (self.state/'tunnel-status.json').write_text(json.dumps({'phase':'ready','checked_at':100,'public_http_status':401,'secret':'PRIVATE','reason':'PRIVATE','connections':'fake'}))
        value=read_record(self.state);self.assertNotIn('PRIVATE',json.dumps(value));self.assertNotIn('connections',value)
        path=self.state/'tunnel-status.json';path.unlink();path.symlink_to(self.state/'other');self.assertEqual(read_record(self.state),{})

    def test_lifecycle_history_accepts_only_fixed_events_and_safe_fields(self):
        record_event(self.state,'connector_started',connector_pid=12,secret='PRIVATE_TOKEN',address='PRIVATE_IP',reason='PRIVATE')
        record_event(self.state,'PRIVATE_EVENT',connector_pid=13)
        path=self.state/'tunnel-events.jsonl';entries=[json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(len(entries),1);self.assertEqual(entries[0]['connector_pid'],12)
        self.assertNotIn('PRIVATE',path.read_text());self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_lifecycle_history_rotates_and_rejects_symlinks(self):
        path=self.state/'tunnel-events.jsonl';path.write_text('x'*(256*1024))
        record_event(self.state,'connector_exited',exit_code=1)
        self.assertEqual((self.state/'tunnel-events.previous.jsonl').stat().st_size,256*1024)
        self.assertLess(path.stat().st_size,512)
        path.unlink();target=self.state/'preserve';target.write_text('keep');path.symlink_to(target)
        record_event(self.state,'connector_started');self.assertEqual(target.read_text(),'keep')


if __name__=='__main__':unittest.main()
