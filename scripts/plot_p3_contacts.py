"""Optional diagnostic plot of recorded P3-A contacts and reconstructed pad boxes.
Uses the existing matplotlib installation; does not reconstruct contact forces.
"""
import argparse
import itertools
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mujoco
import numpy as np


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path(__file__).resolve().parents[1]/'outputs'/'p3a_verification')
    args=parser.parse_args(); folder=args.directory.resolve()
    result=json.loads((folder/'result.json').read_text())
    selected=None
    for line in (folder/'native_contact_samples.jsonl').open():
        sample=json.loads(line)
        if selected is None or abs(sample['solver_step_start_time_s']-9.678)<abs(selected['solver_step_start_time_s']-9.678):
            selected=sample
    model=mujoco.MjModel.from_xml_string((Path(result['enabled']['directory'])/'model.xml').read_text())
    data=mujoco.MjData(model)
    data.qpos[:]=selected['state_at_solver']['qpos'];data.qvel[:]=selected['state_at_solver']['qvel']
    data.ctrl[:]=selected['state_at_solver']['ctrl_native'];mujoco.mj_forward(model,data)
    T=np.asarray(selected['T_world_gripper']);R=T[:3,:3];origin=T[:3,3]
    np.testing.assert_allclose(data.site_xpos[model.site(selected['reference_site']).id],origin,atol=1e-9,rtol=0)
    signs=np.array(list(itertools.product((-1,1),repeat=3)))
    edges=[(a,b) for a in range(8) for b in range(a+1,8) if np.count_nonzero(signs[a]!=signs[b])==1]
    pads=[]
    for name in ('gripper0_finger1_pad_collision','gripper0_finger2_pad_collision'):
        geom=model.geom(name).id
        world=(signs*model.geom_size[geom])@data.geom_xmat[geom].reshape(3,3).T+data.geom_xpos[geom]
        pads.append((world-origin)@R)
    fig,axes=plt.subplots(1,2,figsize=(10,5.2))
    colors=('#cf3f35','#087fb4'); markers=('o','D')
    for ax,indices in zip(axes,((0,1),(1,2))):
        for finger,corners in enumerate(pads):
            for edge_index,(a,b) in enumerate(edges):
                xy=corners[[a,b]][:,indices]*1000
                ax.plot(xy[:,0],xy[:,1],color=colors[finger],alpha=.65,
                        label=f'Finger {finger+1} pad box' if edge_index==0 else None)
        for c in selected['contacts']:
            if c['pair_role']!='finger_target':continue
            finger=0 if c['finger']=='finger_1' else 1
            point=np.asarray(c['position_gripper_m'])[list(indices)]*1000
            normal=np.asarray(c['normal_gripper_finger_to_other'])[list(indices)]*12
            ax.scatter(*point,s=28,color=colors[finger],marker=markers[finger],zorder=4)
            ax.annotate('',xy=point+normal,xytext=point,arrowprops=dict(arrowstyle='->',color=colors[finger],lw=1.5))
        ax.scatter(0,0,color='#202b3b',marker='+',s=70,label='Gripper reference origin',zorder=5)
        ax.set_xlabel('XYZ'[indices[0]]+' in gripper frame / mm');ax.set_ylabel('XYZ'[indices[1]]+' in gripper frame / mm')
        ax.set_aspect('equal',adjustable='datalim');ax.grid(alpha=.2);ax.margins(.22)
    fig.suptitle(f"Actual Panda P3-A contacts at solver t={selected['solver_step_start_time_s']:.3f} s\nDots: recorded solver midpoints | Arrows: recorded normals (12 mm display length)",fontsize=11,y=.99)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.5,.87),ncol=3,frameon=False,fontsize=9)
    fig.tight_layout(rect=(0,0,1,.80))
    fig.savefig(folder/'panda-pad-contact-projections.png',dpi=170)
    (folder/'plotted-contact-sample.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2))
    print('Saved actual-contact projection plot:',folder/'panda-pad-contact-projections.png')


if __name__=='__main__':main()
