# GitHub 上传清单

仓库建议只放能复现实验和部署的文件，不放密钥、运行图片、临时依赖和训练过程缓存。

## 必传

```text
README.md
requirements.txt
.gitignore
GITHUB_UPLOAD_MANIFEST.md
oak_crab_measure.py
crab_thickness.py
thickness_calibration.py
calibrate_thickness.py
depth_thickness_calibration.json
test_depth_block.py
replay_depth.py
upload_queue.py
weight_trigger.py
DEPTH_MEASUREMENT.md
PROJECT_HANDOFF.md
tests/test_crab_thickness.py
tests/test_thickness_calibration.py
tests/test_pipeline_chain.py
tests/test_depth_integration.py
tests/test_pose_quality.py
tests/test_block_depth_cli.py
DEVELOPMENT_LOG.md
OPTIMIZATION_ROADMAP.md
crab_pipeline.py
read_weight_modbus.py
onenet_mqtt_upload.py
onenet_file_upload.py
analyze_repeatability.py
calibrate_plane.py
validate_plane.py
run_once.sh
run_loop.sh
run_repeatability.sh
crab-pipeline.service.example
onenet_mqtt_config.example.json
output/pdf/crab_charuco_5x7_30mm_A4.pdf
output/pdf/crab_charuco_5x7_30mm_spec.json
tools/generate_charuco_pdf.py
tools/generate_charuco_png.py
```

## 模型文件

如果仓库允许放 20MB 左右文件，建议上传当前 OAK 运行模型：

```text
oak_export/best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob
```

如果不想让仓库变大，就不要提交 blob，在 README 里说明需要把该文件手动放到 `~/crab-oak/`。

## 不要上传

```text
onenet_mqtt_config.json
runs/
.venv/
oak_pydeps/
oak_pydeps_blob/
oak_pydeps_min/
__pycache__/
debug_*.jpg
measurement_*.json
calibration_plane.json
calibration_validation.json
*.pt
*.onnx
```

## 上传前本地检查

```powershell
git status --short
```

确认没有真实密钥：

```powershell
rg -n "device_key|file_access_key|Secret|Access" .
```

`PUT_...` 占位符可以保留；真实密钥不能提交。如果命中了 `onenet_mqtt_config.example.json` 里的占位字段是正常的，重点检查是否出现真实设备密钥或真实访问密钥。
