"""Evaluation scene sets: the P4-D plan's own test cases, optionally extended with extra test scenes.

The extra scenes come from the same sampler, ranges and prompt pools as the dataset plan, under a
different seed and an ``-x`` scene-id infix. They are audited together with the full dataset plan
(train/validation included), so no shape instance, layout, held-out family or held-out phrase can
leak between the extra scenes and the training data. They have no recorded demonstration, so
``--reference`` only applies to the dataset's own cases.
"""
from dataclasses import replace

from .p4_dataset import _cases, _scene, audit_plan, canonical_digest

SCENE_SETS = ('dataset', 'extended')
EXTENDED_SEED = 20261112
# Extra scenes per family; with the dataset's 6+6+20 test scenes this gives 60 scenes (120
# episodes) in each test split.
EXTRA_SCENES = {('box_step', 'test_instance'): 24, ('cylinder_step', 'test_instance'): 24,
                ('ellipsoid_step', 'test_unseen'): 40}


def extra_cases(seed=EXTENDED_SEED):
    scenes = []
    for (family, split), count in EXTRA_SCENES.items():
        for index in range(count):
            scene = _scene(seed, family, split, index, held_out=index >= count//2)
            scenes.append(replace(scene, scene_id=f'{family}-{split}-x{index:03d}'))
    return list(_cases(seed, scenes))


def scene_set_cases(name, dataset_cases, dataset_controls):
    """All cases of a scene set (dataset cases first) plus its audit and digest."""
    if name not in SCENE_SETS:
        raise ValueError('scene set must be one of '+', '.join(SCENE_SETS))
    extra = extra_cases() if name == 'extended' else []
    audit = audit_plan(list(dataset_cases)+extra, dataset_controls)
    document = dict(scene_set=name, seed=EXTENDED_SEED if extra else None,
                    extra_scenes={f'{family}/{split}': count for (family, split), count in EXTRA_SCENES.items()}
                    if extra else None, extra_cases=[case.describe() for case in extra])
    return list(dataset_cases)+extra, list(dataset_controls), dict(audit=audit, sha256=canonical_digest(document),
                                                                  extra_cases=len(extra))
