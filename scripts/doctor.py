"""Read-only baseline verification. Run with the original backend Python."""
import hashlib
import importlib.metadata as md
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
EXPECTED_FILES={
    '/home/openvla/projects/show_libero.py':'3f85fdd9e8c4b594702879cbf7e5689cc86d15d8a553e5c7003fcae7e5e22a13',
    '/home/openvla/projects/test_libero_object.py':'f7f34a6d8f2efa238fc5a957cf52c86ca4f9397d4925ffcd5cb281566d674e79',
    '/home/openvla/.libero/config.yaml':'20f2b0fc0e0098cc6dbf302364b8bbe8b23d720fc893802ead483587f0ba773d',
    '/home/openvla/venvs/openvla-oft/pyvenv.cfg':'db6c8a96f25493eda9f74f23f0b5f248a8b50a5b469b15c5ee7313875b416364',
}
EXPECTED_PACKAGES='ea323012208d8a81b73e3561ffe4a08b284220bd13dab4e669ee2a6e45fd4eb6'

def main():
    # Match the original inspection's PYTHONPATH. Source egg-info is visible
    # when LIBERO_ROOT is on sys.path, which otherwise changes distribution
    # enumeration even though site-packages has not changed.
    libero_root=os.getenv('ROBOLAB_LIBERO_ROOT','/home/openvla/projects/LIBERO')
    if libero_root not in sys.path:
        sys.path.insert(0,libero_root)
    packages=sorted((d.metadata['Name'],d.version) for d in md.distributions())
    digest=hashlib.sha256(json.dumps(packages).encode()).hexdigest()
    files={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in EXPECTED_FILES}
    versions={}
    for name in ('mujoco','robosuite','libero','torch','numpy','opencv-python','Pillow'):
        try: versions[name]=md.version(name)
        except md.PackageNotFoundError: versions[name]=None
    gpu=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader'],capture_output=True,text=True,timeout=10)
    git=subprocess.run(['git','-C','/home/openvla/projects/LIBERO','status','--porcelain'],capture_output=True,text=True,timeout=10)
    oft_git=subprocess.run(['git','-C','/home/openvla/projects/openvla-oft','status','--porcelain'],capture_output=True,text=True,timeout=10)
    report={'python':sys.version,'executable':sys.executable,'versions':versions,
            'package_list_sha256':digest,'packages_match_before':digest==EXPECTED_PACKAGES,
            'metadata_libero_source_root':libero_root,
            'files':files,'files_match_before':files==EXPECTED_FILES,
            'display':os.getenv('DISPLAY'),'wayland_display':os.getenv('WAYLAND_DISPLAY'),
            'x11_socket_exists':Path('/mnt/wslg/.X11-unix/X0').exists(),
            'gpu':gpu.stdout.strip(),'gpu_check_ok':gpu.returncode==0,
            'libero_tracked_changes':git.stdout.strip(),'libero_git_clean':git.returncode==0 and not git.stdout.strip(),
            'openvla_tracked_changes':oft_git.stdout.strip(),'openvla_git_clean':oft_git.returncode==0 and not oft_git.stdout.strip()}
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if all(report[k] for k in ('packages_match_before','files_match_before','gpu_check_ok','x11_socket_exists','libero_git_clean')) else 1

if __name__=='__main__': raise SystemExit(main())
