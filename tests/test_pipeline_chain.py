import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import crab_pipeline
from thickness_calibration import fit_linear_calibration
from upload_queue import read_json


class PipelineChainTests(unittest.TestCase):
    def test_depth_calibration_reaches_upload_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out_dir = root / 'runs'
            blob = root / 'model.blob'
            blob.write_bytes(b'test')
            config = root / 'onenet.json'
            config.write_text('{}', encoding='utf-8')
            calibration_path = root / 'depth_thickness_calibration.json'
            calibration_path.write_text(
                json.dumps(
                    fit_linear_calibration(
                        [
                            {'actual_mm': 20, 'measured_mm': 18.5},
                            {'actual_mm': 30, 'measured_mm': 26.0},
                        ]
                    )
                ),
                encoding='utf-8',
            )

            parser = crab_pipeline.build_parser()
            args = parser.parse_args(
                [
                    '--blob', str(blob),
                    '--depth',
                    '--thickness-calibration', str(calibration_path),
                    '--out-dir', str(out_dir),
                    '--config', str(config),
                    '--skip-file-upload',
                    '--weight-port', '',
                ]
            )
            crab_pipeline.validate_arguments(parser, args)
            calls = []

            def fake_runner(command, timeout=120.0):
                calls.append(command)
                if 'oak_crab_measure.py' in command:
                    def after(flag):
                        return Path(command[command.index(flag) + 1])

                    measurement_path = after('--json-out')
                    image_path = after('--raw-image')
                    depth_path = after('--depth-out')
                    measurement_path.parent.mkdir(parents=True, exist_ok=True)
                    measurement_path.write_text(
                        json.dumps(
                            {
                                'measurement_id': 'chain-test',
                                'timestamp': 1.0,
                                'measurement_ok': True,
                                'unit': 'mm',
                                'legs': [],
                                'weight': None,
                                'thickness': {
                                    'ok': True,
                                    'reason': 'ok',
                                    'thickness_mm': 26.0,
                                    'raw_thickness_mm': 26.0,
                                    'calibrated_thickness_mm': 30.0,
                                    'reported_thickness_mm': 30.0,
                                    'thickness_calibration': {
                                        'enabled': True,
                                        'status': 'ok',
                                    },
                                },
                            }
                        ),
                        encoding='utf-8',
                    )
                    image_path.write_bytes(b'jpeg')
                    depth_path.write_bytes(b'npz')
                    return

                if 'onenet_mqtt_upload.py' in command:
                    measurement_path = Path(command[command.index('--measurement') + 1])
                    result_path = Path(command[command.index('--result-out') + 1])
                    result_path.write_text(
                        json.dumps(
                            {
                                'ok': True,
                                'measurement_id': read_json(measurement_path)['measurement_id'],
                                'image_fid': '',
                            }
                        ),
                        encoding='utf-8',
                    )
                    return

                raise AssertionError(f'Unexpected command: {command}')

            with patch.object(crab_pipeline, 'run_command', side_effect=fake_runner):
                measurement_path = crab_pipeline.run_once(args)

            measurement = read_json(measurement_path)
            manifests = list(out_dir.glob('pipeline_*.json'))
            self.assertEqual(len(manifests), 1)
            manifest = read_json(manifests[0])
            self.assertEqual(measurement['thickness']['reported_thickness_mm'], 30.0)
            self.assertTrue(measurement['image_upload']['skipped'])
            self.assertTrue(manifest['mqtt_upload_ok'])
            self.assertEqual(manifest['thickness']['reported_thickness_mm'], 30.0)
            measure_command = calls[0]
            self.assertIn('--depth', measure_command)
            self.assertIn('--thickness-calibration', measure_command)
            self.assertIn(str(calibration_path), measure_command)


if __name__ == '__main__':
    unittest.main()
