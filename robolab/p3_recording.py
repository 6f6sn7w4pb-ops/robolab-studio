"""Write an opt-in contact sidecar alongside a native P2 episode."""
import hashlib
import logging
import math
import os
from pathlib import Path

from .contact_store import (FINAL_P2, FRAME_SCHEMA, MANIFEST, MANIFEST_SCHEMA, MAX_BYTES,
                            MAX_FRAME_BYTES, MAX_SAMPLES, TRACE, canonical_json, validate_alignment)


class ContactRecorder:
    def __init__(self, parent, sampler):
        self.parent = parent
        self.sampler = sampler
        self.directory = parent.directory
        self.stream = None
        self.count = self.size = 0
        self.first = self.last = None
        self.digest = hashlib.sha256()
        source = Path(__file__).parent
        parent_metadata = parent.metadata
        dims = parent_metadata['state_view']['dimensions']
        self.metadata = dict(schema=MANIFEST_SCHEMA, recording_status='recording',
                             episode_id=self.directory.name, trace_file=TRACE, frame_schema=FRAME_SCHEMA,
                             sampler_schema='robolab.p3_contact_sample.v1', original_rgb_recorded=False,
                             dimensions={key: dims[key] for key in ('nq', 'nv', 'nu')},
                             mujoco_version=parent_metadata['versions']['mujoco'],
                             physics_dt_s=parent_metadata['control']['physics_timestep_s'],
                             profile=sampler.profile.description, profile_sha256=sampler.profile.sha256,
                             force_epsilon_N=sampler.force_epsilon_N,
                             source_sha256={name: hashlib.sha256((source/name).read_bytes()).hexdigest()
                                            for name in ('p3_contact.py', 'contact_geometry.py', 'p3_recording.py')})
        try:
            self._publish()
            self.stream = (self.directory/TRACE).open('xb')
        except (OSError, ValueError) as error:
            self.abort(str(error))
            raise RuntimeError('无法创建 P3 接触记录') from error

    def _publish(self):
        temporary = self.directory/(MANIFEST+'.tmp')
        temporary.write_bytes(canonical_json(self.metadata)+b'\n')
        temporary.replace(self.directory/MANIFEST)

    def capture(self, sim_time):
        try:
            sample = self.sampler.latest
            if not sample or sample['schema'] != self.metadata['sampler_schema']:
                raise ValueError('Native contact sample unavailable: '+str(self.sampler.reason))
            if self.parent.count != self.count+2 or sample['profile_sha256'] != self.metadata['profile_sha256']:
                raise ValueError('P3 sample does not belong to this parent step/profile')
            start = validate_alignment(sample, self.count, self.directory.name, sim_time,
                                       self.metadata['physics_dt_s'], self.last if self.last is not None else -math.inf)
            frame = dict(sample, schema=FRAME_SCHEMA)
            line = canonical_json(frame)+b'\n'
            if len(line) > MAX_FRAME_BYTES or self.size+len(line) > MAX_BYTES or self.count >= MAX_SAMPLES:
                raise ValueError('P3 trace exceeds recording limits')
            if self.stream.write(line) != len(line):
                raise OSError('Short P3 contact write')
            if (self.count+1) % 50 == 0:
                self.stream.flush()
            self.digest.update(line)
            self.size += len(line)
            self.count += 1
            if self.first is None:
                self.first = start
            self.last = start
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
            self.abort(str(error))
            raise RuntimeError('P3 接触记录写入失败，已停止采集') from error

    def abort(self, reason):
        """Keep any written prefix for diagnosis, without declaring it complete."""
        if self.metadata['recording_status'] != 'recording':
            return
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError:
                logging.exception('Could not close failed P3 trace')
        self.metadata.update(recording_status='failed', failure_reason=reason, sample_count=self.count)
        try:
            self._publish()
        except OSError:
            logging.exception('Could not persist P3 failure state')

    def summary(self):
        return {'status': self.metadata['recording_status'], 'sample_count': self.count,
                'manifest': str(self.directory/MANIFEST)}

    def finish(self):
        if self.metadata['recording_status'] != 'recording':
            return self.summary()
        parent = self.parent.metadata
        try:
            if parent['status'] not in FINAL_P2 or parent['sample_count'] != self.count+1:
                raise ValueError('P3 cannot finalize without the complete parent row mapping')
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()
            self.metadata.update(recording_status='finalized', sample_count=self.count,
                                 trace_sha256=self.digest.hexdigest(), first_solver_time_s=self.first,
                                 last_solver_time_s=self.last,
                                 parent=dict(schema=parent['schema'], trajectory_sha256=parent['trajectory_sha256'],
                                             model_xml_sha256=parent['state_view']['model_file_sha256'],
                                             sample_count=parent['sample_count'], episode_status=parent['status']))
            self._publish()
        except (OSError, ValueError, KeyError) as error:
            self.metadata['recording_status'] = 'recording'
            self.metadata.pop('trace_sha256', None)
            self.abort(str(error))
        return self.summary()
