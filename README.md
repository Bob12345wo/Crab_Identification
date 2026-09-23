# 螃蟹腿部识别项目

新增 OAK 双目深度厚度测量：使用 `--depth` 启用，参见 [深度测量与现场验证说明](DEPTH_MEASUREMENT.md)。板端文件替换、标定、检测、OneNET 和交付验收请参见 [板端交付与完整检测手册](BOARD_DELIVERY_GUIDE.md)，项目状态参见 [项目交接文档](PROJECT_HANDOFF.md)。

本项目用于在 Orange Pi Zero 3 + OAK-D Lite FF 上完成螃蟹图像采集、YOLO Pose 腿部关键点识别、称重模块读取、毫米/像素腿长计算，并将结果和原始图片上传到 OneNET。

当前主流程已经打通：

- OAK-D Lite FF 在设备端运行 YOLO pose blob，不依赖 Orange Pi 做重推理。
- USB-RS485 读取 Modbus 称重模块，空载小漂移会按死区归零。
- OneNET MQTT 属性上报已成功，文件管理图片上传已成功并返回 `fid`。
- 支持 ChArUco 平面毫米标定。只有独立验证通过后，毫米数据才可作为相对精确结果使用。

## 1. 硬件连接

推荐连接方式：

- Orange Pi Zero 3：使用稳定 5V 电源，建议 5V/3A 或更高质量电源。
- 有源 USB Hub：必须单独插电。
- OAK-D Lite FF：插到有源 USB Hub。
- USB 转 485：插到有源 USB Hub。
- 称重模块：单独按模块要求供电，485 的 A/B/GND 接 USB-RS485 的 A/B/GND。
- 负载传感器：E+、E-、S+、S- 按模块丝印接线。

注意：

- 不建议让 Orange Pi 的单 USB 口同时给 OAK 和 485 设备供电。
- 不要直接拔电断电。先执行 `sudo poweroff`，等板子灯基本停止后再断电。
- 如果换相机位置、焦距、ROI、分辨率或测量平面高度，必须重新做毫米标定。

## 2. 登录板子

电脑和 Orange Pi 在同一个 Wi-Fi 或手机热点下时，先找 IP：

```powershell
arp -a
```
请将电脑或手机热点配置为板子实际使用的 Wi-Fi。Wi-Fi 名称和密码不要写入公开仓库。

如果是 iPhone 热点，常见地址类似 `172.20.10.x`。找到板子后登录：

```powershell
ssh orangepi@<板子IP>
```

如果重刷系统后提示 host key 变化，在 Windows PowerShell 执行：

```powershell
ssh-keygen -R 172.20.10.2
```

板子上检查网络：

```bash
hostname -I
ip route
ping -c 4 8.8.8.8
```

关机：

```bash
sudo poweroff
```

## 3. 新板子环境配置

进入项目目录：

```bash
mkdir -p ~/crab-oak
cd ~/crab-oak
```

安装系统依赖：

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
```

创建 Python 环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

给 USB 串口权限：

```bash
sudo usermod -aG dialout $USER
```

如果 OAK 提示 `Insufficient permissions to communicate with X_LINK_UNBOOTED device`，添加 udev 规则：

```bash
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666", GROUP="plugdev"' | sudo tee /etc/udev/rules.d/80-movidius.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

执行完权限相关操作后，建议重启一次。

## 4. 需要放到板子的文件

最小运行文件：

```text
best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob
oak_crab_measure.py
crab_thickness.py
thickness_calibration.py
calibrate_thickness.py
depth_thickness_calibration.json
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
onenet_mqtt_config.json
requirements.txt
```

建议同时保留：

```text
output/pdf/crab_charuco_5x7_30mm_A4.pdf
output/pdf/crab_charuco_5x7_30mm_spec.json
crab-pipeline.service.example
```

不要上传或提交：

```text
.venv/
runs/
__pycache__/
*.jpg
measurement_*.json
onenet_mqtt_config.json
```

`onenet_mqtt_config.json` 是真实密钥文件，只放在板子上。本仓库只提交 `onenet_mqtt_config.example.json`。

## 5. 从电脑传文件到板子

在 Windows PowerShell 中执行，按你的实际 IP 修改：

```powershell
scp oak_crab_measure.py crab_thickness.py thickness_calibration.py calibrate_thickness.py depth_thickness_calibration.json crab_pipeline.py read_weight_modbus.py onenet_mqtt_upload.py onenet_file_upload.py analyze_repeatability.py calibrate_plane.py validate_plane.py run_once.sh run_loop.sh run_repeatability.sh requirements.txt orangepi@172.20.10.2:~/crab-oak/
scp oak_export\best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob orangepi@172.20.10.2:~/crab-oak/
scp onenet_mqtt_config.example.json orangepi@172.20.10.2:~/crab-oak/onenet_mqtt_config.json
```

板子上授权脚本：

```bash
cd ~/crab-oak
chmod +x run_once.sh run_loop.sh run_repeatability.sh
```

## 6. OneNET 配置

产品建议配置：

- 产品类型：智能摄像机或自定义设备均可。
- 智能化方式：设备接入。
- 节点类型：直连设备。
- 接入协议：MQTT。
- 数据协议：OneJson。
- 联网方式：Wi-Fi。

物模型属性建议：

| 标识符 | 类型 | 范围/长度 | 读写 | 用途 |
| --- | --- | --- | --- | --- |
| `weight_g` | float | 0-10000, step 0.01, unit g | 只读 | 重量 |
| `measurement_ok` | int32 | 0-1 | 只读 | 本次测量是否满足质量门限 |
| `thickness_ok` | int32 | 0-1 | 只读 | 厚度是否满足深度和校准质量门限 |
| `thickness_mm` | float | 厚度毫米 | 只读 | 启用校准时为最终 reported_thickness_mm |
| `image_name` | string | 512 | 只读 | 上传图片文件名 |
| `image_fid` | string | 128 | 只读 | OneNET 文件管理返回的文件 ID |
| `legs_json` | string | 512 | 只读 | 精简腿长数据 |

`onenet_mqtt_config.json` 按下面填：

```json
{
  "host": "mqtts.heclouds.com",
  "port": 1883,
  "product_id": "PUT_PRODUCT_ID_HERE",
  "device_name": "PUT_DEVICE_NAME_HERE",
  "device_key": "PUT_DEVICE_SECRET_HERE",
  "file_access_key": "PUT_USER_OR_PRODUCT_ACCESS_KEY_HERE",
  "token_method": "sha256",
  "token_expire_days": 30,
  "topic_mode": "property",
  "payload_style": "value",
  "file_upload_url": "https://iot-api.heclouds.com/device/file-upload",
  "file_token_version": "2022-05-01",
  "file_token_method": "sha1",
  "file_key_encoding": "base64",
  "file_url_encoding": "quote_plus",
  "file_auth_res": "userid/PUT_NUMERIC_USER_ID_HERE",
  "file_upload_timeout": 30,
  "qos": 0
}
```

说明：

- `product_id`：OneNET 产品 ID。
- `device_name`：设备名，例如 `crab_oak_001`。
- `device_key`：设备详情里的设备密钥，用于 MQTT 登录。
- `file_access_key`：文件上传鉴权使用的访问密钥。当前项目验证成功的是“我的资源 -> 访问权限”里的密钥组合对应值。
- `file_auth_res`：文件上传成功时使用的是 `userid/<数字用户ID>`，不是 `CM...` 账号名。你的数字用户 ID 可在控制台账号卡片里看到。

判断 OneNET 成功：

- MQTT 成功：返回里有 `"code":200,"msg":"success"`。
- 文件上传成功：返回里有 `"code":0,"msg":"succ"`，并且有 `fid`。

## 7. 硬件自检

进入环境：

```bash
cd ~/crab-oak
source .venv/bin/activate
```

检查 USB：

```bash
lsusb
ls /dev/ttyUSB*
```

正常应能看到：

- OAK：`03e7:2485 Intel Movidius MyriadX`
- USB-RS485：常见为 `1a86:7523 QinHeng CH340`
- 串口：`/dev/ttyUSB0`

检查 OAK：

```bash
python - <<'PY'
import depthai as dai
print(dai.Device.getAllAvailableDevices())
PY
```

检查称重：

```bash
python read_weight_modbus.py --port /dev/ttyUSB0 --baud 9600 --repeat 10
```

空载建议先清零：

```bash
python read_weight_modbus.py --port /dev/ttyUSB0 --baud 9600 --tare
```

## 8. 一键运行

主入口：

```bash
cd ~/crab-oak
source .venv/bin/activate
./run_once.sh
```

启用 OAK 厚度和当前系统误差校准：

```bash
./run_once.sh --depth --thickness-calibration depth_thickness_calibration.json
```

运行后会做这些事：

1. OAK 采集 5 帧。
2. 选择质量最好的一帧。
3. 读取称重模块。
4. 计算腿长。
5. 保存原始图片和 debug 图片。
6. 将图片上传 OneNET 文件管理，拿到 `fid`。
7. 将重量、腿长、图片名、`fid` 通过 MQTT 上报到 OneNET 属性。

输出文件在 `runs/`：

```text
measurement_*.json       本次完整测量结果
debug_*.jpg              带关键点的调试图
crab_*_W...jpg           原始上传图，文件名内含精简数据
onenet_file_*.json       文件上传返回
onenet_upload_*.json     MQTT 属性上报返回
pipeline_*.json          本次流程汇总
pending_*.json           上传失败时保留的待处理记录
```

图片名格式示例：

```text
crab_20260923_120000_ab12cd34ef56_W20.5g_TH30.0mm_MM_LC185.9_LT24.4_LA27.3_LB39.8_LG68.1_RC194.8_RT24.3_RA29.2_RB44.6_RG79.1_Q1111111111.jpg
```

其中：

- `W0.0g`：重量。
- `TH30.0mm`：通过质量检查后的最终厚度，优先使用校准后的 `reported_thickness_mm`。
- `THNA`：未启用深度、未识别到螃蟹、深度质量失败或厚度超出校准范围，不能当作 0 mm。
- `MM`/`PX`：腿长使用毫米或像素单位，与厚度单位无关。
- `MM` 或 `PX`：单位。
- `LC/LT/LA/LB/LG/RC/RT/RA/RB/RG`：左右爪和腿长。
- `Q1111111111`：10 条腿/爪的可靠性位，1 可靠，0 不可靠。

## 9. 查看数据

板子本地查看：

```bash
ls -lh runs | tail
cat runs/pipeline_最新时间.json
cat runs/measurement_最新时间.json
cat runs/onenet_upload_最新时间.json
```

复制图片或结果回电脑：

```powershell
scp orangepi@172.20.10.2:~/crab-oak/runs/debug_20260702_100804.jpg .
scp orangepi@172.20.10.2:~/crab-oak/runs/measurement_20260702_100804.json .
```

OneNET 控制台查看：

- 设备接入管理 -> 设备管理 -> 选择设备 -> 属性。
- `weight_g` 看重量。
- `measurement_ok` 看本次是否通过质量门限。
- `thickness_ok` 看厚度是否通过深度质量和校准范围检查。
- `thickness_mm` 为最终上报厚度；原始值和校准诊断保存在本地 measurement JSON。
- `image_name` 看上传文件名。
- `image_fid` 用于在文件管理/下载接口定位图片。
- `legs_json` 是精简腿长数据。

## 10. 毫米标定

没有标定时，腿长单位只能是像素 `px`。需要真实毫米数据时，必须做平面标定。

使用项目里的标定板：

```text
output/pdf/crab_charuco_5x7_30mm_A4.pdf
```

打印要求：

- A4 纸 100% 实际大小打印。
- 不要适应页面、不要缩放。
- 用尺子量底部 100mm 检查线，尽量接近 100mm。

标定步骤：

1. 固定相机、光照、ROI、测量平台。
2. 把 ChArUco 标定板平放在螃蟹测量平面上。
3. 标定板尽量位于螃蟹平时出现的位置。
4. 执行：

```bash
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

成功时会看到类似：

```text
Saved calibration -> calibration_plane.json
quality: corners=20 inliers=20 rms=0.083mm p95=0.132mm ...
```

独立验证：

1. 不移动相机。
2. 把标定板在同一平面上移动或旋转到另一个位置。
3. 执行：

```bash
python validate_plane.py \
  --calibration calibration_plane.json \
  --output calibration_validation.json \
  --preview calibration_validation_preview.jpg
```

如果可见角点只有 10 个，可先诊断：

```bash
python validate_plane.py \
  --calibration calibration_plane.json \
  --output calibration_validation.json \
  --preview calibration_validation_preview.jpg \
  --min-corners 10
```

毫米数据可用的标准：

- `validation: ok=True`
- `rms` 建议小于 0.8mm
- `p95` 建议小于 1.5mm
- `scale` 建议在 0.99-1.01 之间

验证没过时，不要相信毫米数据。常见原因是标定板没有放在同一测量平面、纸张翘曲、反光、相机位置变了、ROI 改了。

只要 `calibration_plane.json` 在项目目录里，`run_once.sh` 会自动使用它，输出单位会变成 `mm`。

## 11. 重复性测试

固定相机、光照、目标位置、称重台状态，执行：

```bash
./run_repeatability.sh 20
```

输出示例：

```text
runs=20 measurement_ok=20/20 (100.0%) unit=px
L-Claw reliable=20/20 mean=501.38px cv=0.23% range=0.77% pass=True
overall_pass=True
```

判断：

- `measurement_ok` 应接近 100%。
- 每条腿 `cv` 建议小于 2%。
- 每条腿 `range` 建议小于 5%。

重复性通过只能说明稳定，不代表毫米绝对准确。毫米准确性仍然取决于标定验证。

## 12. 调参与优化表

| 问题 | 现象 | 优先检查 | 调整位置 | 合格目标 |
| --- | --- | --- | --- | --- |
| 无法识别螃蟹 | `no_detection_above_threshold`，score 很低 | 原始图是否清晰、螃蟹是否在 ROI 中、是否和训练图差异太大 | `run_once.sh` 的 `--roi-scale`、`--min-score`、模型 blob | score 稳定大于 0.3，10 条腿大部分可靠 |
| 点偏离腿末端 | debug 图关键点没落在腿尖 | 标注质量、训练集是否覆盖真实拍摄角度、图像是否过暗或反光 | 重新标注/训练，保持真实相机场景 | 末端点稳定落在对应腿尖 |
| 只有部分腿可靠 | `Q` 中有 0，`unreliable_legs` 不为空 | 是否被遮挡，是否末端置信度低 | `--kpt-conf`、`--min-reliable-legs`、训练集增强 | 至少 8/10 可靠，目标是 10/10 |
| 爪子被判不合理 | `implausible_segment_ratio` | 爪子天然节段不均匀 | `--claw-max-segment-ratio` | 爪子误判减少，但不能放太宽 |
| 普通腿被判不合理 | 普通腿出现 ratio 过大 | 是否关键点串腿或端点错位 | `--max-segment-ratio`，优先修模型 | 普通腿不应大量靠放宽阈值通过 |
| 毫米不准 | px 稳定但 mm 偏差大 | ChArUco 验证是否通过、平面高度是否一致 | 重新标定/验证 | `validate_plane.py` 通过 |
| 空载有重量 | 空载显示几克以内 | 是否没清零、平台漂移、电源不稳 | `--weight-zero-deadband`、`--tare` | 空载最终上报 0g |
| OneNET 有属性没图片 | MQTT 成功但无 `fid` | 文件上传返回 code | `onenet_mqtt_config.json` 文件上传字段 | 文件上传 `code=0` 且有 `fid` |
| 上传失败 | `pending_*.json` 出现 | 网络、密钥、OneNET 返回码 | 配置和网络 | MQTT code=200，文件 code=0 |

调参原则：

- 先看 `runs/debug_*.jpg`，不要只看数值。
- 先保证拍摄结构稳定，再调阈值。
- 阈值只能减少误判，不能修正关键点本身错误。
- 真正要提高腿长精度，最终要靠更贴近真实场景的数据重新训练。

## 13. 后续优化方向

建议顺序：

1. 固定真实拍摄结构：相机高度、光照、测量平面、螃蟹摆放范围。
2. 完成毫米标定并让独立验证通过。
3. 用真实相机采集 100-300 张不同姿态螃蟹图，重新标注腿端关键点。
4. 用当前脚本批量收集失败样本，把 `debug_*.jpg` 作为返修依据。
5. 重新训练并导出 OAK blob，替换 `best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob`。
6. 再跑重复性测试和毫米标定验证。

真实螃蟹注意：

- 平面标定测的是投影到平台上的长度。
- 如果腿明显翘起，真实 3D 长度会比平面投影长。
- 想更接近真实腿长，应让腿尽量贴近测量平面，或后续引入深度/多视角修正。

## 14. 后台运行

可用 systemd 托管循环运行。先复制示例：

```bash
sudo cp crab-pipeline.service.example /etc/systemd/system/crab-pipeline.service
sudo systemctl daemon-reload
sudo systemctl enable crab-pipeline.service
sudo systemctl start crab-pipeline.service
```

查看日志：

```bash
journalctl -u crab-pipeline.service -f
```

停止：

```bash
sudo systemctl stop crab-pipeline.service
```

正式部署前建议先把 `crab-pipeline.service.example` 里的 `ExecStart` 改成和 `run_once.sh` 一致的参数，避免后台运行和手动运行配置不一致。
