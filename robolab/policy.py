"""Read-only VLA checkpoint inspection and real HTTP inference transport.

No torch/transformers import, weight allocation or download is performed here.
The HTTP protocol is RoboLab's bridge protocol, not an assumed OpenVLA/openpi API.
"""
import json
import math
import base64
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

DEFAULT_MODEL='moojink/openvla-7b-oft-finetuned-libero-object'

def scan_checkpoints(extra_roots=()):
    from .config import ROOT, OFT_ROOT
    roots=[Path.home()/'.cache'/'huggingface'/'hub', ROOT.parent, OFT_ROOT, ROOT/'models']
    for key in ('HF_HOME','HF_HUB_CACHE','HUGGINGFACE_HUB_CACHE','TRANSFORMERS_CACHE'):
        if os.getenv(key):
            root=Path(os.environ[key]).expanduser()
            roots.extend([root,root/'hub'])
    roots.extend(Path(p).expanduser() for p in extra_roots)
    visited=set(); results=[]; errors=[]
    for root in roots:
        if not root.is_dir(): continue
        for folder,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[n for n in dirs if n not in ('.git','.venv','.venv-sim','venvs','node_modules','datasets','__pycache__')]
            if 'config.json' not in files: continue
            candidate=Path(folder).resolve()
            if candidate in visited: continue
            visited.add(candidate)
            try: results.append(inspect_checkpoint(candidate))
            except (ValueError,OSError,json.JSONDecodeError) as error: errors.append({'path':str(candidate),'reason':str(error)})
    return {'roots':[{'path':str(p),'exists':p.is_dir()} for p in dict.fromkeys(roots)],
            'checkpoints':results,'complete_candidates':sum(x['files_present'] for x in results),'rejected':errors,
            'downloaded':False,'model_loaded':False}

def validate_observation(observation):
    if not isinstance(observation,dict) or observation.get('schema')!='robolab.observation.v1':
        raise ValueError('Unsupported observation schema')
    if not isinstance(observation.get('instruction'),str) or not observation['instruction'].strip():
        raise ValueError('Observation needs a task instruction')
    for key,count in [('proprio',8),('joints',7)]:
        values=observation.get(key)
        if not isinstance(values,list) or len(values)!=count or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in values):
            raise ValueError(f'{key} must contain {count} finite values')
    images=observation.get('images',{})
    for name in ('agentview','robot0_eye_in_hand'):
        value=images.get(name,{})
        if value.get('encoding')!='jpeg_base64' or not isinstance(value.get('data'),str):
            raise ValueError('Missing RGB camera: '+name)
        raw=base64.b64decode(value['data'],validate=True)
        if not raw.startswith(b'\xff\xd8') or len(raw)>2_000_000:
            raise ValueError('Invalid JPEG image: '+name)
    return observation

@dataclass
class ModelStatus:
    stage:str='configured'
    family:str='openvla_oft'
    real_model:bool=True
    checkpoint:str=''
    model_loaded:bool=False
    real_inference_verified:bool=False
    detail:str='仅配置；尚未找到或加载模型权重'

    def as_dict(self): return dict(self.__dict__)

class VlaAdapter:
    """Replaceable model interface. It has no dependency on a simulator or Qt.

    Observation and action spaces are declared at the boundary. Future pi05,
    geometric fusion and gripper-contact implementations register a new factory
    or wrap an adapter without changing the GUI/physics loop.
    """
    action_space='libero_osc_7d'
    def status(self): raise NotImplementedError
    def load(self): raise NotImplementedError
    def predict(self,observation): raise NotImplementedError
    def close(self): pass

class MockPolicy(VlaAdapter):
    display_name='Mock Policy · 无模型自检'
    def __init__(self): self.calls=0
    def status(self):
        return {'stage':'mock_ready','family':'mock','real_model':False,'model_loaded':False,
                'real_inference_verified':False,'detail':'Mock 自检：无 VLA 权重，仅测试观察/动作/物理接口'}
    def load(self): return self.status()
    def predict(self,observation):
        validate_observation(observation); self.calls+=1
        # Small upward Cartesian commands through LIBERO OSC, not joint angles.
        actions=[[0.,0.,.10,0.,0.,0.,-1.] for _ in range(4)]
        return {'actions':actions,'action_space':self.action_space,'is_mock':True,'source':'mock',
                'real_inference':False,'observation_id':observation['observation_id']}

class OpenVLAOFTAdapter(VlaAdapter):
    display_name='OpenVLA-OFT · 首个模型适配器'
    def __init__(self,checkpoint='',endpoint='',timeout=10):
        self.model_status=ModelStatus(checkpoint=checkpoint)
        self.endpoint=endpoint
        self.transport=HttpPolicy(endpoint,timeout) if endpoint else None
        self.inspection=None
        if checkpoint:
            self.inspection=inspect_checkpoint(checkpoint)
            self.model_status.stage='weights_found' if self.inspection['files_present'] else 'configured'
            self.model_status.detail='权重文件已找到，尚未加载或验证张量' if self.inspection['files_present'] else '权重目录不完整：'+', '.join(self.inspection['missing'])
    def status(self): return self.model_status.as_dict()
    def load(self):
        if not self.transport: raise ValueError('请设置独立的 OpenVLA-OFT 推理服务地址；本操作不会自动下载或占用本机 GPU')
        health=self.transport.connect()
        if health.get('family')!='openvla_oft' or health.get('test_backend') is True:
            raise ValueError('该服务不是已加载真实 OpenVLA-OFT 的服务；Mock 不能标记为真实模型')
        if health.get('model_loaded') is not True: raise ValueError('服务尚未加载 OpenVLA-OFT 权重')
        self.model_status.stage='model_loaded'; self.model_status.model_loaded=True
        self.model_status.detail='服务报告模型已加载；等待一次真实推理验证'
        return self.status()
    def predict(self,observation):
        validate_observation(observation)
        if not self.model_status.model_loaded: raise ValueError('模型尚未加载')
        result=self.transport.predict(observation)
        if result.get('real_inference') is not True or result.get('is_mock') is not False:
            raise ValueError('响应未确认真实模型推理；不会将 Mock 结果标记为 VLA 推理通过')
        self.model_status.stage='inference_verified'; self.model_status.real_inference_verified=True
        self.model_status.detail='真实推理请求返回了有效的 LIBERO 7 维动作'
        return result
    def close(self):
        if self.transport: self.transport.close()
        self.model_status.model_loaded=False; self.model_status.real_inference_verified=False
        self.model_status.stage='weights_found' if self.inspection and self.inspection['files_present'] else 'configured'

VLA_ADAPTERS={'mock':MockPolicy,'openvla_oft':OpenVLAOFTAdapter}

def register_vla_adapter(name,factory):
    if not name or name in VLA_ADAPTERS or not callable(factory): raise ValueError('Invalid or duplicate VLA adapter')
    VLA_ADAPTERS[name]=factory

def create_vla_adapter(name,**configuration):
    if name not in VLA_ADAPTERS: raise ValueError('VLA adapter is not installed: '+str(name))
    return VLA_ADAPTERS[name](**configuration)

def adapter_catalog():
    return [{'id':key,'label':getattr(factory,'display_name',key)} for key,factory in VLA_ADAPTERS.items()]

def load_adapter_plugins():
    """Optional installed entry points. No package is installed by RoboLab."""
    from importlib.metadata import entry_points
    for entry in entry_points(group='robolab.vla_adapters'):
        register_vla_adapter(entry.name,entry.load())

def inspect_checkpoint(path):
    root=Path(path).expanduser().resolve()
    if not root.is_dir(): raise ValueError('模型目录不存在；请选择包含 config.json 的本地权重目录')
    config_path=root/'config.json'
    if not config_path.is_file(): raise ValueError('模型目录中没有 config.json')
    if config_path.stat().st_size>2_000_000: raise ValueError('模型配置过大')
    config=json.loads(config_path.read_text(encoding='utf-8'))
    if config.get('model_type') not in ('openvla','prismatic'):
        raise ValueError('当前本地导入检查支持 OpenVLA/OFT；openpi 请使用独立推理服务')
    weights=list(root.glob('model*.safetensors'))+list(root.glob('pytorch_model*.bin'))
    missing=[]
    for index in list(root.glob('*.safetensors.index.json'))+list(root.glob('pytorch_model.bin.index.json')):
        manifest=json.loads(index.read_text(encoding='utf-8'))
        for name in set(manifest.get('weight_map',{}).values()):
            candidate=(root/name).resolve()
            if not candidate.is_relative_to(root): raise ValueError('模型索引引用了目录外的文件')
            if not candidate.is_file(): missing.append(name)
    for name in ('dataset_statistics.json','preprocessor_config.json','tokenizer_config.json'):
        if not (root/name).is_file(): missing.append(name)
    if not any((root/name).is_file() for name in ('tokenizer.json','tokenizer.model')): missing.append('tokenizer.json or tokenizer.model')
    for component in ('action_head','proprio_projector'):
        candidates=[p for p in root.glob('*'+component+'*') if p.is_file() and p.suffix in ('.pt','.bin','.safetensors') and p.stat().st_size>0]
        if not candidates: missing.append(component+' checkpoint (OFT)')
    if not weights: missing.append('model weight shards')
    empty=[p.name for p in weights if p.stat().st_size==0]
    missing.extend('empty: '+name for name in empty)
    return {'family':'openvla_oft','path':str(root),'model_type':config.get('model_type'),
            'weight_files':len(weights),'weight_bytes':sum(p.stat().st_size for p in weights),
            'missing':sorted(set(missing)),'files_present':not missing,
            'model_loaded':False,'status':'checkpoint_inspected',
            'note':'仅检查文件，不加载 GPU，不验证张量内容；真实模型加载需推理服务确认'}

def validate_actions(value):
    if not isinstance(value,list) or not 1<=len(value)<=32: raise ValueError('服务必须返回 1–32 个动作')
    actions=[]
    for row in value:
        if not isinstance(row,list) or len(row)!=7: raise ValueError('LIBERO 动作必须是 7 维，不能把动作当作关节角')
        if any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) for x in row):
            raise ValueError('动作中包含非有限数值')
        if any(abs(x)>1.00001 for x in row): raise ValueError('服务返回了超出 LIBERO OSC [-1,1] 的动作')
        actions.append([float(x) for x in row])
    return actions

class HttpPolicy:
    def __init__(self,endpoint,timeout=10):
        parsed=urlparse(endpoint)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('请输入 http(s) 服务地址，不要在地址中嵌入密码或令牌')
        self.endpoint=endpoint.rstrip('/')
        self.timeout=float(timeout)
        self.health=None

    def _request(self,path,payload=None):
        body=None if payload is None else json.dumps(payload,allow_nan=False).encode()
        request=Request(self.endpoint+path,data=body,headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=self.timeout) as response:
            data=response.read(2_000_001)
        if len(data)>2_000_000: raise ValueError('推理响应过大')
        result=json.loads(data)
        if not isinstance(result,dict): raise ValueError('推理响应必须是 JSON 对象')
        if result.get('error'): raise ValueError(str(result['error']))
        return result

    def connect(self):
        health=self._request('/health')
        if health.get('service')!='robolab-vla' or health.get('protocol')!=1:
            raise ValueError('服务不是 RoboLab VLA 桥接协议 v1；普通网页不能作为推理服务')
        if health.get('action_space')!='libero_osc_7d': raise ValueError('服务动作空间与 LIBERO 不匹配')
        self.health=health
        return health

    def predict(self,observation):
        if not self.health or self.health.get('model_loaded') is not True:
            raise ValueError('服务尚未真实加载模型')
        result=self._request('/predict',observation)
        if result.get('action_space')!='libero_osc_7d': raise ValueError('推理响应动作空间不匹配')
        result['actions']=validate_actions(result.get('actions'))
        return result

    def close(self): self.health=None

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Read-only VLA checkpoint inspection; no model loading or download')
    parser.add_argument('--scan',action='store_true')
    parser.add_argument('--checkpoint')
    args=parser.parse_args()
    if not args.scan and not args.checkpoint: parser.error('Specify --scan or --checkpoint')
    print(json.dumps(scan_checkpoints() if args.scan else inspect_checkpoint(args.checkpoint),indent=2,ensure_ascii=False))
