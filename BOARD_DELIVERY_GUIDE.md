# 螃蟹腿部识别项目：板端交付与完整检测手册

更新时间：2026-09-23  
适用设备：Orange Pi Zero 3、OAK-D Lite FF、USB-RS485 称重模块、OneNET

## 1. 系统输出

完整流程为：

```text
称重 -> OAK RGB/深度采集 -> YOLO Pose 关键点 -> 腿长毫米换算
-> 背壳高度测量与校准 -> 保存本地结果 -> 上传原始图片
-> OneNET MQTT 属性上报
```

主要输出在 `~/crab-oak/runs/`：

| 文件 | 内容 |
| --- | --- |
| `measurement_*.json` | 完整测量、质量状态、腿长、重量和厚度 |
| `debug_*.jpg` | 检测框、关键点和厚度取样区域 |
| `crab_*.jpg` | 上传 OneNET 的无标注原始图片 |
| `depth_*.npz` | 对齐后的原始深度和内参 |
| `onenet_file_*.json` | 图片上传结果和 `fid` |
| `onenet_upload_*.json` | MQTT 上报和平台回复 |
| `pipeline_*.json` | 本次完整流程汇总 |
| `pending_*.json` | 网络失败后等待重传的任务 |

图片名示例：

```text
crab_20260923_120000_ab12cd34ef56_W20.5g_TH30.0mm_MM_LC185.9_..._Q1111111111.jpg
```

- `W20.5g`：重量。
- `TH30.0mm`：有效且校准后的最终背壳高度。
- `THNA`：没有有效厚度，不代表 0 mm。
- `MM`/`PX`：腿长单位。
- `LC...RG`：左右钳和腿长摘要。
- `Q` 后 10 位：对应部位可靠性，`1` 可靠，`0` 不可靠。

## 2. 交付文件清单

从电脑项目目录传到板子的运行文件：

```text
oak_crab_measure.py
crab_thickness.py
thickness_calibration.py
calibrate_thickness.py
test_depth_block.py
crab_pipeline.py
read_weight_modbus.py
onenet_mqtt_upload.py
onenet_file_upload.py
upload_queue.py
weight_trigger.py
analyze_repeatability.py
calibrate_plane.py
validate_plane.py
run_once.sh
run_loop.sh
run_repeatability.sh
requirements.txt
oak_export/best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob
depth_thickness_calibration.json
```

板子上必须保留且不能被模板覆盖：

```text
onenet_mqtt_config.json
calibration_plane.json
calibration_validation.json
.venv/
runs/
```

`onenet_mqtt_config.json` 含真实密钥，不能提交 GitHub，也不要发送给无关人员。

## 3. 无损更新板端文件

### 3.1 在板子备份关键配置

```bash
cd ~/crab-oak
backup_dir="deploy_backup_$(date +%Y%m%d_%H%M%S)"
mkdir "$backup_dir"

for file in onenet_mqtt_config.json calibration_plane.json calibration_validation.json depth_thickness_calibration.json; do
  if [[ -f "$file" ]]; then
    cp -a "$file" "$backup_dir/"
  fi
done

echo "backup saved: $backup_dir"
```

### 3.2 从 Windows 覆盖程序文件

把 `<板子IP>` 替换为实际地址：

```powershell
cd C:\Users\26492\Desktop\yolo

scp oak_crab_measure.py `
  crab_thickness.py `
  thickness_calibration.py `
  calibrate_thickness.py `
  test_depth_block.py `
  crab_pipeline.py `
  read_weight_modbus.py `
  onenet_mqtt_upload.py `
  onenet_file_upload.py `
  upload_queue.py `
  weight_trigger.py `
  analyze_repeatability.py `
  calibrate_plane.py `
  validate_plane.py `
  run_once.sh `
  run_loop.sh `
  run_repeatability.sh `
  requirements.txt `
  depth_thickness_calibration.json `
  orangepi@<板子IP>:~/crab-oak/

scp oak_export\best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob `
  orangepi@<板子IP>:~/crab-oak/best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob
```

不要把 `onenet_mqtt_config.example.json` 覆盖成板端的 `onenet_mqtt_config.json`。

### 3.3 板端更新依赖

```bash
cd ~/crab-oak
source .venv/bin/activate
python -m pip install -r requirements.txt
chmod +x run_once.sh run_loop.sh run_repeatability.sh
```

## 4. 首次登录与硬件检查

```powershell
ssh orangepi@<板子IP>
```

板端检查：

```bash
cd ~/crab-oak
source .venv/bin/activate
lsusb
lsusb -t
ls -l /dev/ttyUSB*
```

应能看到：

- OAK：USB VID `03e7`。
- USB-RS485：常见 CH340，VID:PID `1a86:7523`。
- 称重串口：通常为 `/dev/ttyUSB0`。

OAK 与 USB-RS485 建议连接带独立电源的 USB Hub。连续出现 `X_LINK_ERROR`、断开重连或设备崩溃，优先检查 Hub 外部电源、USB 数据线和接口。

单独检查 OAK：

```bash
python - <<'PY'
import depthai as dai
devices = dai.Device.getAllAvailableDevices()
print("devices:", devices)
if devices:
    with dai.Device(devices[0]) as device:
        print("usb speed:", device.getUsbSpeed())
PY
```

单独检查称重：

```bash
python read_weight_modbus.py --port /dev/ttyUSB0 --baud 9600 --repeat 10
```

需要清零时：

```bash
python read_weight_modbus.py --port /dev/ttyUSB0 --baud 9600 --tare
```

## 5. OneNET 配置

现有产品物模型至少包含：

| 标识符 | 类型 | 说明 |
| --- | --- | --- |
| `weight_g` | float | 重量，单位 g |
| `measurement_ok` | int32 | 本次整体测量是否有效 |
| `thickness_ok` | int32 | 本次厚度是否有效 |
| `thickness_mm` | float | 最终校准厚度，单位 mm |
| `image_name` | string | 图片文件名 |
| `image_fid` | string | OneNET 文件 ID |
| `legs_json` | string | 精简腿长与可靠性数据 |

新增或修改物模型后，需要在控制台保存并发布新版本。设备不需要删除重建。

板端配置基于 `onenet_mqtt_config.example.json`，真实配置文件名必须是：

```text
~/crab-oak/onenet_mqtt_config.json
```

关键字段：

```json
{
  "host": "mqtts.heclouds.com",
  "port": 1883,
  "product_id": "产品ID",
  "device_name": "设备名称",
  "device_key": "设备密钥",
  "file_access_key": "文件上传访问密钥",
  "file_auth_res": "userid/数字用户ID",
  "file_token_version": "2022-05-01",
  "file_token_method": "sha1",
  "file_key_encoding": "base64",
  "file_url_encoding": "quote_plus"
}
```

成功标准：

```text
文件上传：code=0、msg=succ、存在 fid
MQTT：connect.subscribed=true、reply code=200、platform_accepted=true
```

## 6. 腿长平面标定

腿长毫米换算使用 ChArUco 平面标定。标定板规格：

```text
DICT_4X4_50
5 x 7 squares
square = 30 mm
marker = 22 mm
打印尺寸 = 150 x 210 mm
打印比例 = 100%
```

相机、ROI、测量平面确定后，将标定板平放在真实螃蟹放置平面，执行：

```bash
cd ~/crab-oak
source .venv/bin/activate

python calibrate_plane.py \
  --output calibration_plane.json \
  --preview calibration_plane_preview.jpg \
  --input-size 640 \
  --camera-source-size 1920 \
  --roi-scale 0.42 \
  --roi-center-x 0.50 \
  --roi-center-y 0.50 \
  --frames 30
```

标定通过要求：

```text
RMS <= 0.5 mm
P95 <= 1.0 mm
横向和纵向覆盖比例均 >= 30%
```

不要移动相机和测量平面。把标定板在同一平面内换一个位置和方向，独立验证：

```bash
python validate_plane.py \
  --calibration calibration_plane.json \
  --output calibration_validation.json \
  --preview calibration_validation_preview.jpg
```

验证通过要求：

```text
ok=true
RMS <= 0.8 mm
P95 <= 1.5 mm
尺度误差 <= 1%
```

如果只检测到少量角点，调整标定板位置、光照和清晰度，不要单纯降低验收标准。

## 7. 深度厚度标定

厚度定义为“放置平面到背壳取样区的垂直高度”，不是腹壳到背壳的直接壳体厚度。

固定最终相机、ROI 和测量平面后，分别放置用卡尺确认过的 20 mm 和 30 mm 哑光标准块。每个高度都必须重新采集，不能共用一份深度文件。

采集 20 mm 标准块：

```bash
python test_depth_block.py \
  --capture \
  --frames 10 \
  --warmup-frames 20 \
  --image block_20mm_rgb.jpg \
  --depth block_20mm_depth.npz
```

从 `block_20mm_rgb.jpg` 获取标准块左上角和右下角坐标，再分析：

```bash
python test_depth_block.py \
  --depth block_20mm_depth.npz \
  --bbox X1 Y1 X2 Y2 \
  --height-mm 20 \
  --output block_20mm_result.json
```

30 mm 重复相同流程：

```bash
python test_depth_block.py \
  --capture \
  --frames 10 \
  --warmup-frames 20 \
  --image block_30mm_rgb.jpg \
  --depth block_30mm_depth.npz

python test_depth_block.py \
  --depth block_30mm_depth.npz \
  --bbox X1 Y1 X2 Y2 \
  --height-mm 30 \
  --output block_30mm_result.json
```

用两份合格报告生成有边界的厚度校准：

```bash
python calibrate_thickness.py \
  --result block_20mm_result.json \
  --result block_30mm_result.json \
  --output depth_thickness_calibration.json
```

默认禁止在校准范围外外推。若真实螃蟹厚度超出 20--30 mm，应增加覆盖实际范围的标准块并重新标定，不应直接启用 `--allow-extrapolation`。

相机、安装高度、角度、分辨率、ROI、Depth 参数或测量平面发生变化后，平面标定和厚度标定都必须重新验证；厚度标定至少重新采集标准块。

## 8. 单次完整检测

```bash
cd ~/crab-oak
source .venv/bin/activate

./run_once.sh \
  --depth \
  --thickness-calibration depth_thickness_calibration.json
```

运行后查看最新结果：

```bash
measurement_file="$(ls -t runs/measurement_*.json | head -1)"
pipeline_file="$(ls -t runs/pipeline_*.json | head -1)"
file_result="$(ls -t runs/onenet_file_*.json | head -1)"
mqtt_result="$(ls -t runs/onenet_upload_*.json | head -1)"

echo "$measurement_file"
cat "$pipeline_file"
cat "$file_result"
cat "$mqtt_result"
```

完整通过应满足：

```text
measurement_ok=true
thickness.ok=true
thickness.reported_thickness_mm 有值
file_upload_ok=true
mqtt_upload_ok=true
文件上传 code=0 且有 fid
MQTT platform_accepted=true 且 reply code=200
```

没有螃蟹时，`measurement_ok=0`、`thickness_ok=0` 和图片名 `THNA` 是正常的，但 OneNET MQTT 本身仍应成功接收这条无效状态记录。

## 9. 连续检测

每 30 秒运行一次：

```bash
./run_loop.sh 30 \
  --depth \
  --thickness-calibration depth_thickness_calibration.json
```

停止：

```text
Ctrl+C
```

正式后台运行前，应修改 `crab-pipeline.service.example` 中的 `ExecStart`：

```text
ExecStart=/bin/bash /home/orangepi/crab-oak/run_loop.sh 30 --depth --thickness-calibration depth_thickness_calibration.json
```

安装服务：

```bash
sudo cp crab-pipeline.service.example /etc/systemd/system/crab-pipeline.service
sudo systemctl daemon-reload
sudo systemctl enable --now crab-pipeline.service
sudo systemctl status crab-pipeline.service
```

查看日志：

```bash
journalctl -u crab-pipeline.service -f
```

## 10. 交付验收

交付前至少完成：

1. 最终安装位置重新验证平面标定。
2. 20 mm、30 mm 标准块厚度复测。
3. 多只真实螃蟹与卡尺高度对比。
4. 空载称重归零和已知砝码检查。
5. 原始图片上传并取得 `fid`。
6. OneNET 收到 `weight_g`、`measurement_ok`、`thickness_ok`、`thickness_mm`、`image_name`、`image_fid` 和 `legs_json`。
7. 断网后产生 `pending_*.json`，恢复网络后可以补传。
8. 连续运行，确认 OAK 不出现重复 `X_LINK_ERROR`，磁盘文件按 `--keep` 正常轮换。

## 11. 常见问题

### `No available devices`

OAK 未连接、被其他程序占用或无 USB 权限。检查 `lsusb`、线缆、Hub 电源和 udev 规则。

### 重复 `X_LINK_ERROR`

通常是供电或 USB 链路不稳定。给有源 Hub 接独立电源，更换数据线或接口，并单独连接 OAK 测试。

### 称重 `stable=0/3`

检查 `/dev/ttyUSB0`、RS485 A/B/GND、模块供电和波特率。设备节点可能变为 `/dev/ttyUSB1`。

### `pose_quality_failed`

没有螃蟹、螃蟹不在 ROI、画面模糊、姿态或外观与训练集差异过大。先检查 `raw` 和 `debug` 图片，不要直接降低阈值。

### `thickness_calibration_out_of_range`

测量结果超出标准块覆盖范围。增加对应高度标准块并重新标定。

### 文件上传成功但 MQTT 失败

文件上传与 MQTT 是两条独立链路。检查最新 `onenet_upload_*.json`：应有 `subscribed=true`、回复 `code=200`。确认板端已部署最新版 `onenet_mqtt_upload.py`，且 OneNET 物模型已经保存并发布。

## 12. 安全关机

```bash
sudo poweroff
```

等待板子状态灯基本停止后，再断开电源。
