"""P0：只读检查当前环境，报告写到项目 outputs；不安装依赖或加载模型。"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as md
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def run(command, env=None, timeout=60):
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, errors='replace', timeout=timeout)
        return {'command':command, 'returncode':result.returncode, 'stdout':result.stdout, 'stderr':result.stderr}
    except subprocess.TimeoutExpired as error:
        return {'command':command,'returncode':None,'error':f'timeout after {timeout}s',
                'stdout':str(error.stdout or ''),'stderr':str(error.stderr or '')}
    except OSError as error:
        return {'command':command,'returncode':None,'error':str(error)}

def git_info(path):
    return {'path':str(path),'head':run(['git','-C',str(path),'rev-parse','HEAD']),
            'status':run(['git','-C',str(path),'status','--porcelain'])}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--libero-root',type=Path,default=Path(os.getenv('LIBERO_ROOT',os.getenv('ROBOLAB_LIBERO_ROOT',str(Path.home()/'projects'/'LIBERO')))))
    parser.add_argument('--oft-root',type=Path,default=Path(os.getenv('OPENVLA_OFT_ROOT',str(Path.home()/'projects'/'openvla-oft'))))
    parser.add_argument('--output',type=Path,default=ROOT/'outputs'/'env_audit'/'before.json')
    args=parser.parse_args()
    lib=args.libero_root.expanduser().resolve(); oft=args.oft_root.expanduser().resolve()
    config=Path(os.getenv('LIBERO_CONFIG_PATH',str(Path.home()/'.libero')))/'config.yaml'
    versions={}
    for name in ('mujoco','robosuite','libero','openvla-oft','torch','numpy','opencv-python','Pillow','PySide6','transformers','tensorflow','protobuf','tensorflow-metadata','bitsandbytes'):
        try: versions[name]=md.version(name)
        except md.PackageNotFoundError: versions[name]=None
    distributions=sorted(set((d.metadata['Name'].lower(),d.version) for d in md.distributions()))
    digest=hashlib.sha256(json.dumps(distributions).encode()).hexdigest()
    protected=[config,Path(sys.prefix)/'pyvenv.cfg',lib.parent/'show_libero.py',lib.parent/'test_libero_object.py',
               lib/'libero'/'libero'/'envs'/'env_wrapper.py',oft/'experiments'/'robot'/'openvla_utils.py']
    files={str(p):{'exists':p.is_file(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None} for p in protected}
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',HF_DATASETS_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    env['PYTHONPATH']=os.pathsep.join([str(ROOT),str(lib),str(oft),env.get('PYTHONPATH','')])
    core_code="""import importlib,json,sys
result={}
for name in ['mujoco','robosuite','libero.libero','torch']:
    try:
        m=importlib.import_module(name); result[name]={'ok':True,'file':getattr(m,'__file__',None)}
    except Exception as e: result[name]={'ok':False,'error':repr(e)}
if result.get('torch',{}).get('ok'):
    import torch
    result['cuda']={'available':torch.cuda.is_available(),'torch_cuda':torch.version.cuda}
print('AUDIT_JSON='+json.dumps(result))
raise SystemExit(0 if all(result[n]['ok'] for n in ['mujoco','robosuite','libero.libero','torch']) else 1)
"""
    core=run([sys.executable,'-c',core_code],env=env) if config.is_file() else {'returncode':None,'error':'LIBERO config missing; import skipped to avoid creating user files'}
    # Import tests are deliberately CPU-only and offline. No from_pretrained() call.
    cpu_env=dict(env,CUDA_VISIBLE_DEVICES='',TF_CPP_MIN_LOG_LEVEL='2')
    oft_code="""import importlib,json,traceback
result={}
for name in ['prismatic','experiments.robot.openvla_utils']:
    try:
        m=importlib.import_module(name); result[name]={'ok':True,'file':getattr(m,'__file__',None)}
    except Exception as e: result[name]={'ok':False,'error':repr(e),'traceback':traceback.format_exc()}
print('AUDIT_JSON='+json.dumps(result))
raise SystemExit(0 if all(x['ok'] for x in result.values()) else 1)
"""
    oft_import=run([sys.executable,'-c',oft_code],env=cpu_env,timeout=90) if config.is_file() else {'returncode':None,'error':'LIBERO config missing; OFT import skipped to avoid an interactive LIBERO import'}
    direct_urls={}
    for name in ('libero','openvla-oft','transformers'):
        try:
            value=md.distribution(name).read_text('direct_url.json')
            direct_urls[name]=json.loads(value) if value else None
        except (md.PackageNotFoundError,ValueError): direct_urls[name]=None
    report={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version,'executable':sys.executable,
            'prefix':sys.prefix,'os':platform.platform(),'project_root':str(ROOT),'versions':versions,
            'libero_root':str(lib),'oft_root':str(oft),'libero_config':str(config),
            'libero_config_text':config.read_text() if config.is_file() else None,
            'protected_files':files,'package_versions_sha256':digest,'package_versions':distributions,
            'editable_install_metadata':direct_urls,
            'git':{'project':git_info(ROOT),'libero':git_info(lib),'openvla_oft':git_info(oft)},
            'pip_check':run([sys.executable,'-m','pip','check'],env=env),
            'core_imports':core,'openvla_oft_imports_cpu_offline':oft_import,
            'gpu':run(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader']),
            'memory':run(['free','-h']),'display_environment':{k:os.getenv(k) for k in ('DISPLAY','WAYLAND_DISPLAY','MUJOCO_GL','PYOPENGL_PLATFORM')},
            'project_files':sorted(str(p.relative_to(ROOT)) for directory in ('robolab','scripts','tests','docs') for p in (ROOT/directory).glob('**/*') if p.is_file() and '__pycache__' not in p.parts),
            'claims':{'installed_or_upgraded_packages':False,'model_weights_loaded':False,'vla_inference_tested':False}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(args.output.resolve()),'python':sys.version.split()[0],'versions':versions,
                      'pip_check_exit':report['pip_check']['returncode'],'core_import_exit':core['returncode'],
                      'oft_import_exit':oft_import['returncode']},ensure_ascii=False,indent=2))
    return 0 if core['returncode']==0 and report['pip_check']['returncode']==0 and oft_import['returncode']==0 else 1

if __name__=='__main__': raise SystemExit(main())
