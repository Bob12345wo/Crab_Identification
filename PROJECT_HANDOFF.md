# 螃蟹识别项目交接文档

更新时间：2026-09-21
仓库：`Bob12345wo/Crab_Identification`
当前分支：`main`

## 1. 当前结论

软件链路已经完成并通过本地自动化验证：

```text
OAK 同步采集
-> RGB/NN/Depth 对齐
-> YOLO Pose 螃蟹识别
-> 背壳区域深度测量
-> 厚度系统误差校准
-> 深度和识别质量判断
-> 保存 RGB、Debug、Depth NPZ、Measurement JSON
-> 上传任务持久化
-> OneNET 图片上传
-> OneNET MQTT 属性上传
```

当前还不能宣称“整机正式验收完成”，原因是以下项目需要连接真实设备和当前 OneNET 账号验证：真实 OAK 采集、最终安装环境、真实螃蟹、称重模块、RS485 和 OneNET 网络。

## 2. 厚度定义

当前算法测量的是：

```text
放置平面到螃蟹背壳取样区域的垂直高度
```

它不是腹壳与背壳两面的直接壳体厚度。螃蟹腹部悬空、腿支撑身体、放置面不平或背壳 ROI 错误，都会影响结果。

## 3. 当前校准配置

文件：`depth_thickness_calibration.json`

当前配置由同一套 OAK、安装位置、分辨率、Depth 参数、ROI 和测量平面下的 20 mm、30 mm 标准块结果生成：

```text
calibrated_mm = 1.3307500944624837 * raw_mm - 4.702001725481672
raw valid range = 18.562464754480683 .. 26.077023680015976 mm
actual range = 20 .. 30 mm
allow_extrapolation = false
```

这不是通用相机标定。只要更换相机、安装高度、角度、分辨率、Depth 设置、RGB/Depth ROI 或测量平面，就必须重新采集标准块并生成新的校准配置。

当前 10 mm 样本低于深度分辨率，报告 `thickness_below_depth_resolution`，不参与校准。

## 4. 已完成验证

### 20 mm 标准块

- 原始中位数：`18.5625 mm`
- 校准后：`20.0000 mm`
- 有效帧：`9/10`
- 深度有效率：`1.0`
- 平面 RMSE：`0.5281 mm`
- 结果：通过

### 30 mm 标准块

- 原始中位数：`26.0770 mm`
- 校准后：`30.0000 mm`
- 有效帧：`10/10`
- 深度有效率：`1.0`
- 平面 RMSE：约 `0.963 mm`
- 原始结果有 `reference_error_too_high`，校准后通过

### 软件验证

```text
32 项自动化测试全部通过
端到端模拟链路测试通过
crab_pipeline.py --help 通过
oak_crab_measure.py --help 通过
```

端到端模拟测试覆盖校准参数传递、本地 measurement JSON、上传待处理任务、pipeline manifest 和 MQTT 上传确认，但不等同于真实 OneNET 网络测试。

## 5. 常用测试命令

在 Windows 项目根目录执行：

```powershell
cd C:\Users\Administrator\Desktop\crab\Crab_Identification
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

使用已有 20 mm 深度样本离线验证校准：

```powershell
.\.venv\Scripts\python.exe test_depth_block.py --depth .\block_20mm_depth.npz --bbox 304 265 365 327 --height-mm 20 --thickness-calibration .\depth_thickness_calibration.json --output .\runs\current_20mm_check.json
```

使用已有 30 mm 深度样本离线验证校准：

```powershell
.\.venv\Scripts\python.exe test_depth_block.py --depth .\block_30mm_depth.npz --bbox 303 261 391 351 --height-mm 30 --thickness-calibration .\depth_thickness_calibration.json --output .\runs\current_30mm_check.json
```

如果有 OAK 但没有螃蟹，可以先采集标准块：

```powershell
.\.venv\Scripts\python.exe test_depth_block.py --capture --frames 10 --image .\block_rgb.jpg --depth .\block_depth.npz
```

打开 `block_rgb.jpg` 记录方块左上角和右下角坐标，再使用 `--bbox X1 Y1 X2 Y2` 分析。临时环境的结果用于评估设备，不要直接当作最终安装环境的校准配置。

## 6. 板端完整运行

把以下文件部署到 Orange Pi 项目目录：

```text
oak_crab_measure.py
crab_thickness.py
thickness_calibration.py
depth_thickness_calibration.json
crab_pipeline.py
read_weight_modbus.py
onenet_mqtt_upload.py
onenet_file_upload.py
upload_queue.py
weight_trigger.py
run_once.sh
run_loop.sh
requirements.txt
best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob
onenet_mqtt_config.json
```

单次完整运行：

```bash
cd ~/crab-oak
source .venv/bin/activate
./run_once.sh --depth --thickness-calibration depth_thickness_calibration.json
```

连续运行：

```bash
./run_loop.sh 30 --depth --thickness-calibration depth_thickness_calibration.json
```

如果不启用深度，不要传入 `--thickness-calibration`。程序现在会直接拒绝这种错误组合，避免校准文件被静默忽略。

## 7. OneNET 平台验收

物模型至少需要这些属性：

| 标识符 | 类型 | 含义 |
| --- | --- | --- |
| `measurement_ok` | int32 | 本次整体测量是否通过 |
| `thickness_ok` | int32 | 厚度是否通过深度和校准质量检查 |
| `thickness_mm` | float | 最终上报厚度，使用 `reported_thickness_mm` |
| `image_name` | string | 图片文件名 |
| `image_fid` | string | 图片上传返回的 fid |
| `legs_json` | string | 腿长和可靠性摘要 |

真实平台整链路验收命令：

```bash
./run_once.sh --depth --thickness-calibration depth_thickness_calibration.json
```

检查 `runs/` 下的结果：

```text
onenet_file_*.json：ok=true，并且有 fid
onenet_upload_*.json：ok=true，并且收到 reply code=200
pipeline_*.json：file_upload_ok=true，mqtt_upload_ok=true
measurement_*.json：thickness_ok 对应本次厚度状态
```

上传图片名也包含厚度摘要：有效厚度写作 `TH<reported_thickness_mm>mm`，例如 `TH30.0mm`；无有效厚度写作 `THNA`。文件名只用于快速识别，正式数据和失败原因以 measurement JSON 与 OneNET 的 `thickness_ok`、`thickness_mm` 为准。

在 OneNET 控制台确认：

```text
thickness_ok=1
thickness_mm=校准后的厚度
image_fid=本次图片返回的 fid
```

厚度失败或超出校准范围时，应该看到 `thickness_ok=0`，不能把 0 mm 当作有效厚度。云端可能保留上一次 `thickness_mm`，所以必须结合 `thickness_ok` 判断。

## 8. 最终设备验收清单

- 固定最终相机位置、角度、分辨率、Depth 参数和测量平面。
- 在最终环境重新采集 20 mm、30 mm 标准块，并重新生成校准文件。
- 如果螃蟹目标高度超出 20--30 mm，增加覆盖目标范围的已知高度标准块。
- 中心和四角重复采集标准块，记录误差、P95、标准差、有效帧比例和平面 RMSE。
- 使用多只真实螃蟹，用卡尺记录对应高度并与 `reported_thickness_mm` 配对。
- 验证螃蟹姿态、腿悬空、反光、遮挡、错误 ROI、无目标和多目标场景。
- 验证称重、RS485、图片上传、MQTT、断网待上传和恢复补传。
- 连续运行并检查 `runs/` 磁盘空间、日志、文件轮换和重启恢复。
- 验收通过后再冻结校准 JSON 和部署版本。

## 9. 数据和安全注意事项

- `onenet_mqtt_config.json` 含真实密钥，只放在设备上，不提交 GitHub。
- `runs/`、运行图片、深度 NPZ 和真实测试结果不作为程序代码提交。
- 不要把 `block_*_result.json` 或深度文件当成通用校准依据；它们绑定具体设备和摆放环境。
- 每次更换设备或测量环境，都要在 `DEVELOPMENT_LOG.md` 记录校准来源、测试结果和新配置文件。

## 10. 当前下一步

下一步不是继续修改测厚公式，而是在最终安装环境执行标准块复测，然后运行一次真实螃蟹完整流程和 OneNET 平台验收。只有这三部分都通过，才能把深度功能标记为当前设备版本的正式完成：

```text
最终环境标准块校准
-> 真实螃蟹与卡尺对比
-> OneNET 真实上传验收
```
