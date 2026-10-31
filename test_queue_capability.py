import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from companion import Bridge, ControlError
from desktop_runtime import photo_queue_supported


def write_schema(directory, *, method=True, image=True):
    root=Path(directory);(root/'v2').mkdir()
    methods={'oneOf':[{'properties':{'method':{'enum':['thread/queue/add' if method else 'other']}}}]}
    params={'properties':{'input':{'items':{'$ref':'#/definitions/UserInput'}}},
            'definitions':{'UserInput':{'oneOf':[{'properties':{'type':{'enum':['localImage' if image else 'text']}},'required':['path','type']}]}}}
    (root/'ClientRequest.json').write_text(json.dumps(methods))
    (root/'v2/ThreadQueueAddParams.json').write_text(json.dumps(params))


class PhotoQueueCapabilityTests(unittest.TestCase):
    def test_photo_queue_uses_advertised_rpc_without_cli_image_flag(self):
        calls=[]
        def runner(args,**options):
            calls.append(args)
            if args[-1]=='--help':return subprocess.CompletedProcess(args,0,'--thread --message','')
            self.assertEqual(options['timeout'],3)
            write_schema(args[-1])
            return subprocess.CompletedProcess(args,0)
        with tempfile.TemporaryDirectory() as home,patch.dict(os.environ,{'CODEX_HOME':home}),patch('companion.shutil.which',return_value='/fake/codex'):
            bridge=Bridge(home,runner=runner)
            with patch('companion.queue_photo_message',return_value={'state':'queued'}) as send:
                for _ in range(2):self.assertEqual(bridge.send('fixture','Photo',photos=['/fake/photo.jpg'])['state'],'queued')
            self.assertEqual(send.call_count,2)
            self.assertEqual(len(calls),2)  # Help plus one cached read-only schema probe.
            self.assertNotIn('--image',calls[1])

    def test_cli_image_flag_does_not_override_an_unsupported_rpc_schema(self):
        def runner(args,**options):
            if args[-1]=='--help':return subprocess.CompletedProcess(args,0,'--thread --message --image','')
            write_schema(args[-1],image=False)
            return subprocess.CompletedProcess(args,0)
        with tempfile.TemporaryDirectory() as home,patch.dict(os.environ,{'CODEX_HOME':home}),patch('companion.shutil.which',return_value='/fake/codex'):
            bridge=Bridge(home,runner=runner)
            with patch('companion.queue_photo_message') as send,self.assertRaises(ControlError):bridge.send('fixture','Photo',photos=['/fake/photo.jpg'])
            send.assert_not_called()
            self.assertTrue(bridge.availability()['available'])

    def test_unavailable_or_changed_schemas_fail_closed(self):
        for case in ['missing-method','missing-image','missing-file','invalid-json','wrong-shape','wrong-reference','missing-fields','symlink','oversized','failed']:
            def runner(args,**options):
                root=Path(args[-1])
                if case=='failed':return subprocess.CompletedProcess(args,1)
                if case=='missing-file':return subprocess.CompletedProcess(args,0)
                write_schema(root,method=case!='missing-method',image=case!='missing-image')
                if case=='invalid-json':(root/'ClientRequest.json').write_text('{invalid')
                if case=='wrong-shape':(root/'ClientRequest.json').write_text('[]')
                if case=='wrong-reference':
                    path=root/'v2/ThreadQueueAddParams.json'
                    path.write_text(path.read_text().replace('#/definitions/UserInput','#/definitions/Unknown'))
                if case=='missing-fields':(root/'v2/ThreadQueueAddParams.json').write_text('{}')
                if case=='symlink':
                    (root/'ClientRequest.json').unlink()
                    (root/'ClientRequest.json').symlink_to(root/'v2/ThreadQueueAddParams.json')
                if case=='oversized':
                    with (root/'ClientRequest.json').open('wb') as handle:handle.truncate(8*1024*1024+1)
                return subprocess.CompletedProcess(args,0)
            with self.subTest(case=case):self.assertFalse(photo_queue_supported('/fake/codex',runner))
        for error in [OSError('private fixture'),subprocess.TimeoutExpired('fixture',3)]:
            self.assertFalse(photo_queue_supported('/fake/codex',Mock(side_effect=error)))

    def test_capability_probe_requires_method_and_local_image_contract(self):
        def runner(args,**options):
            write_schema(args[-1])
            return subprocess.CompletedProcess(args,0)
        self.assertTrue(photo_queue_supported('/fake/codex',runner))


if __name__=='__main__':unittest.main()
