"""Explicit, optional OpenVLA-OFT service; never started by the GUI.

--check inspects only local files. --serve is required for actual tensor loading.
No checkpoint is downloaded. Source checkpoints and installed libraries are not
modified; OFT's metadata-sync helper operates on a project-owned staged copy.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
from http.server import BaseHTTPRequestHandler,HTTPServer
from .config import ROOT, OFT_ROOT
from .policy import inspect_checkpoint,validate_observation,validate_actions

def image_for_oft(canonical_rgb):
    """Match installed robosuite/OpenVLA helpers without coupling the simulator.

    Renderer returns flipud(raw OpenGL). robosuite opengl observations are raw;
    OFT rotates those 180 deg. Thus canonical -> OFT is a horizontal flip.
    """
    return canonical_rgb[:,::-1].copy()

def stage_checkpoint(source):
    source=Path(source).resolve()
    dest=ROOT/'model-cache'/hashlib.sha256(str(source).encode()).hexdigest()[:16]
    dest.mkdir(parents=True,exist_ok=True)
    for path in source.iterdir():
        if not path.is_file(): continue
        target=dest/path.name
        if path.suffix in ('.safetensors','.bin','.pt','.pth'):
            if not target.exists(): target.symlink_to(path.resolve())
        else:
            if target.is_symlink(): target.unlink()
            shutil.copy2(path,target)
    return dest

class NativeOFT:
    def __init__(self,checkpoint,source,precision):
        os.environ['HF_HUB_OFFLINE']='1'; os.environ['TRANSFORMERS_OFFLINE']='1'
        sys.path.insert(0,str(Path(source).resolve()))
        os.chdir(source)
        import tensorflow as tf
        tf.config.set_visible_devices([],'GPU')
        from experiments.robot import openvla_utils
        # This service accepts a local checkpoint only; no Hub probing/download.
        openvla_utils.model_is_on_hf_hub=lambda path:False
        from experiments.robot.libero.run_libero_eval import GenerateConfig,initialize_model,process_action
        from experiments.robot.robot_utils import get_action
        self.cfg=GenerateConfig(pretrained_checkpoint=str(stage_checkpoint(checkpoint)),task_suite_name='libero_object',
                                load_in_4bit=precision=='int4',load_in_8bit=precision=='int8')
        self.model,self.head,self.proprio,self.noisy,self.processor=initialize_model(self.cfg)
        self.get_action,self.process_action=get_action,process_action

    def predict(self,observation):
        import numpy as np
        from PIL import Image
        validate_observation(observation)
        def image(name):
            raw=base64.b64decode(observation['images'][name]['data'],validate=True)
            return image_for_oft(np.asarray(Image.open(io.BytesIO(raw)).convert('RGB')))
        obs={'full_image':image('agentview'),'wrist_image':image('robot0_eye_in_hand'),
             'state':np.asarray(observation['proprio'],dtype=np.float32)}
        actions=self.get_action(self.cfg,self.model,obs,observation['instruction'],processor=self.processor,
                                action_head=self.head,proprio_projector=self.proprio,noisy_action_projector=self.noisy)
        actions=[np.clip(self.process_action(np.asarray(a),self.cfg.model_family),-1,1).tolist() for a in actions]
        return {'actions':validate_actions(actions),'action_space':'libero_osc_7d','real_inference':True,
                'is_mock':False,'observation_id':observation['observation_id']}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',required=True)
    parser.add_argument('--source',default=str(OFT_ROOT))
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--precision',choices=['int4','int8','bf16'],default='int4')
    parser.add_argument('--host',default='127.0.0.1'); parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    report=inspect_checkpoint(args.checkpoint)
    print(json.dumps(report,indent=2,ensure_ascii=False),flush=True)
    if args.check or not args.serve: return 0 if report['files_present'] else 2
    if not report['files_present']: raise ValueError('Incomplete OFT checkpoint; no tensors were loaded')
    native=NativeOFT(args.checkpoint,args.source,args.precision)
    class Handler(BaseHTTPRequestHandler):
        def reply(self,value,status=200):
            body=json.dumps(value,allow_nan=False).encode()
            self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if self.path!='/health': self.reply({'error':'not found'},404); return
            self.reply({'service':'robolab-vla','protocol':1,'family':'openvla_oft','action_space':'libero_osc_7d',
                        'model_loaded':True,'test_backend':False,'checkpoint':str(args.checkpoint)})
        def do_POST(self):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.path!='/predict' or not 0<length<=5_000_000: raise ValueError('Invalid request')
                self.reply(native.predict(json.loads(self.rfile.read(length))))
            except Exception as error: self.reply({'error':str(error)},400)
    server=HTTPServer((args.host,args.port),Handler)
    print(f'OpenVLA-OFT loaded; serving {args.host}:{args.port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0

if __name__=='__main__': raise SystemExit(main())
