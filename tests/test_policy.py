import json
import tempfile
import unittest
import threading
import subprocess
import sys
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from robolab.policy import (OpenVLAOFTAdapter,inspect_checkpoint,validate_actions,
                            create_vla_adapter,register_vla_adapter,MockPolicy,HttpPolicy)

class AdapterTests(unittest.TestCase):
    def test_missing_and_incomplete_weights_never_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'config.json').write_text(json.dumps({'model_type':'openvla'}))
            before=(root/'config.json').read_bytes()
            result=inspect_checkpoint(root)
            self.assertFalse(result['files_present']); self.assertFalse(result['model_loaded'])
            adapter=OpenVLAOFTAdapter(checkpoint=tmp)
            self.assertEqual(adapter.status()['stage'],'configured')
            with self.assertRaises(ValueError): adapter.load()
            self.assertEqual((root/'config.json').read_bytes(),before)
    def test_checkpoint_presence_not_model_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('config.json','dataset_statistics.json','preprocessor_config.json','tokenizer_config.json','tokenizer.json'):
                (root/name).write_text(json.dumps({'model_type':'openvla'}))
            # File-presence fixture, explicitly not real model tensors.
            for name in ('model.safetensors','action_head--fixture.pt','proprio_projector--fixture.pt'):
                (root/name).write_bytes(b'fixture-not-a-real-weight')
            adapter=OpenVLAOFTAdapter(checkpoint=tmp)
            self.assertEqual(adapter.status()['stage'],'weights_found')
            self.assertFalse(adapter.status()['model_loaded'])
            self.assertFalse(adapter.status()['real_inference_verified'])
    def test_action_space_validation(self):
        for bad in ([[0]*6],[[float('nan')]*7],[[2]*7],[],[[True]*7]):
            with self.subTest(value=bad),self.assertRaises(ValueError): validate_actions(bad)
        self.assertEqual(len(validate_actions([[0]*7])[0]),7)
    def test_alternate_adapter_registry(self):
        register_vla_adapter('custom_pipeline_fixture',MockPolicy)
        adapter=create_vla_adapter('custom_pipeline_fixture')
        self.assertFalse(adapter.status()['real_model'])
        self.assertEqual(adapter.action_space,'libero_osc_7d')
    def test_endpoint_validation(self):
        for url in ('file:///etc/passwd','https://u:p@example.com','http://','https://example.com/?token=secret'):
            with self.subTest(url=url),self.assertRaises(ValueError): HttpPolicy(url)

    def test_http_contract_does_not_promote_test_server_to_real_model(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body=json.dumps({'service':'robolab-vla','protocol':1,'family':'openvla_oft','model_loaded':True,
                                 'test_backend':True,'action_space':'libero_osc_7d'}).encode()
                self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self,*args): pass
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            endpoint=f'http://127.0.0.1:{server.server_port}'
            transport=HttpPolicy(endpoint); self.assertTrue(transport.connect()['test_backend'])
            adapter=OpenVLAOFTAdapter(endpoint=endpoint)
            with self.assertRaises(ValueError): adapter.load()
            self.assertFalse(adapter.status()['model_loaded'])
            self.assertFalse(adapter.status()['real_inference_verified'])
        finally: server.shutdown(); server.server_close(); thread.join()

    def test_bridge_import_does_not_import_model_runtimes(self):
        process=subprocess.run([sys.executable,'-c',
            'import sys; import robolab.openvla_service; assert "torch" not in sys.modules; assert "tensorflow" not in sys.modules'],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(process.returncode,0,process.stderr)

    def test_oft_preprocessing_matches_raw_opengl_helper(self):
        import numpy as np
        from robolab.openvla_service import image_for_oft
        raw=np.arange(4*5*3,dtype=np.uint8).reshape(4,5,3)
        canonical=np.flipud(raw)
        official_oft=raw[::-1,::-1]
        np.testing.assert_array_equal(image_for_oft(canonical),official_oft)
        np.testing.assert_array_equal(canonical,np.flipud(raw))

if __name__=='__main__': unittest.main(verbosity=2)
