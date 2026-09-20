# OAK 深度与螃蟹厚度测量

## 无螃蟹时测试标准块

用卡尺测量哑光 3D 打印方块的实际高度，并将方块平放在实际测量面上。电脑可通过 USB 直连 OAK 运行此命令；若 OAK 仍接在 Orange Pi 上，则先在板子运行，再把 `block_rgb.jpg` 和 `block_depth.npz` 复制到电脑离线分析。无需 blob、称重模块或 OneNET 配置。

在 Windows 电脑上，把 OAK 的 USB 数据线接到电脑；同一时刻只能由电脑或板子之一打开相机。在本项目目录的 PowerShell 中准备环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe test_depth_block.py --capture --frames 10
```

电脑直连时不需要运行 `run_once.sh`。相机保持在真实测量位置（镜头到放置面可先用约 55 cm），光轴尽量垂直；方块周围保留足够的同一平面。采集时默认丢弃启动后的前 15 组同步帧，待双目深度稳定后再保存 `--frames` 指定的帧数。首次采集成功会得到 `block_rgb.jpg` 和 `block_depth.npz`。如果脚本提示未找到 OAK，先检查数据线、供电和相机是否仍被板子占用。

```bash
python test_depth_block.py --capture
```

打开 `block_rgb.jpg`（640 x 640），读出方块外接矩形的像素坐标。方块四周应留有足够的同一平面；不要把盒壁或其他高度的背景框进周围区域。比如方块实际高 20.3 mm、边界为 (250, 260) 到 (380, 390)：

```bash
python test_depth_block.py --depth block_depth.npz --bbox 250 260 380 390 --height-mm 20.3
```

结果保存在 `block_result.json`：`thickness_mm` 是有效帧估计高度的中位数，`error_mm` 是相对卡尺读数的误差，`std_mm` 和 `range_mm` 是同一次采集的帧间波动，`valid_depth_ratio` 是方块中央采样区平均有效深度比例，`plane_inlier_ratio` 和 `plane_rmse_mm` 反映周围平面质量。`frames` 列出每一帧的结果，至少 80% 帧有效才会给出有效汇总高度。若 `ok=false`，先检查每帧 `reason`，不要直接放宽阈值。这里的中央取样区默认占方块外接框宽高的 35%，可用 `--body-scale` 调整；测试方块时应确保该区域完全落在方块平坦顶面。
`thickness_mm` 使用有效帧中位数，`mean_thickness_mm` 保留均值用于诊断；默认要求至少 80% 帧有效、有效帧极差不超过 5 mm、与 `--height-mm` 的误差不超过 2 mm。误差超限时仍会保存厚度值，并将 `reason` 标为 `reference_error_too_high`，便于定位系统误差。

## 厚度系统误差校准

标准块验证发现，当前 OAK、安装位置、RGB/Depth ROI 和测量平面下存在稳定的系统偏差。校准工具只拟合已知标准块的系统误差，不会替代平面质量、深度有效率和帧稳定性检查。

使用已经采集的 20 mm 和 30 mm 结果生成配置：

```bash
python calibrate_thickness.py \
  --result block_20mm_result.json \
  --result block_30mm_result.json \
  --output depth_thickness_calibration.json
```

当前仓库中的配置由同一台 OAK 和同一套摆放参数得到，关系为：

```text
calibrated_mm = 1.3307500944624837 * raw_mm - 4.702001725481672
raw_mm valid range = 18.562464754480683 .. 26.077023680015976
```

该配置只适用于生成它的相机、安装位置、分辨率、Depth 设置、RGB/Depth ROI 和测量平面。默认禁止外推：原始厚度落在校准范围外时，结果会标记 `thickness_calibration_out_of_range`，不会输出未经验证的修正值。没有 50 mm 标准块也可以先完成 20/30 mm 两点校准；这只能修正当前范围内的线性系统偏差，后续拿到第三个高度时应重新拟合并检查残差。

离线使用校准配置：

```bash
python test_depth_block.py \
  --depth block_20mm_depth.npz \
  --bbox 304 265 365 327 \
  --height-mm 20 \
  --thickness-calibration depth_thickness_calibration.json
```

测量 JSON 同时保留原始值和最终值：

- `thickness_mm`、`raw_thickness_mm`：未校准的深度几何结果，用于诊断兼容性。
- `calibrated_thickness_mm`：应用校准后的结果。
- `reported_thickness_mm`：系统和 OneNET 应使用的最终厚度。
- `raw_ok`、`raw_quality_reasons`：校准前的质量判断，不能因为校准而丢失。
- `thickness_calibration.status`：成功时为 `ok`，超出范围时为 `below_calibration_range` 或 `above_calibration_range`；未启用时为 `disabled`，没有可校准厚度时为 `no_raw_measurement`。

例如 30 mm 标准块原始结果约为 26.08 mm 时，原始质量仍会记录 `reference_error_too_high`，校准后约为 30.00 mm，并通过 `calibration_reference_ok`；原始误差在 `error_mm`，校准后误差在 `calibrated_error_mm`。这两类字段都应保留，便于判断是深度质量问题还是系统偏差。

建议分别测量 10、20、30 mm 左右的方块，并在画面中心和四角重复采样。拍摄时方块不能接触画面边缘，底面要贴合测量面。同一位置的一次采集可用 `--frames 10` 比较帧间稳定性；要比较不同位置，分别执行 `--capture` 并指定不同文件名（`--image`、`--depth`、`--output`）。旧的单帧 NPZ 仍可回放。

新增功能适用于本项目的 OAK-D Lite 双目相机和 DepthAI 3.7.1。默认关闭，使用 --depth 启用。

## 测量含义与摆放

输出 thickness.thickness_mm 为背壳取样区相对放置平面的未校准垂直高度，单位毫米；启用厚度校准后，业务使用 thickness.reported_thickness_mm。
程序将毫米深度反投影到三维，在检测框外拟合支撑平面，再计算中央背壳区域点到平面的距离，取第 90 百分位，降低孤立噪点影响。
这不是腹壳与背壳两面的直接测量。腹部悬空、腿撑起身体或托盘背景高度不同都会使结果偏离身体实际厚度。

相机固定在上方。让腹部贴近水平、哑光、有纹理的放置面，周围保留充足的同一平面。
检测框外不要出现托盘外的低桌面、其他物品或另一只螃蟹，否则可能拟合到错误平面。
检查调试图中的青色矩形是否落在背壳内；默认取检测框中央 35%，可通过 --depth-body-scale 调整。
当前模型的背壳关键点定义未确认，因此不使用未知关键点自动圈定背壳。

## 单次本地测量

在安装好 requirements.txt 的设备上运行，不需要称重或云端连接：

```bash
python oak_crab_measure.py \
  --blob oak_export/best_yolo_rgb_scale255_imgsz640_openvino_2022.1_4shave.blob \
  --depth --frame-count 5 \
  --thickness-calibration depth_thickness_calibration.json \
  --json-out measurement_depth.json \
  --depth-out depth_sample.npz \
  --debug-image depth_debug.jpg
```

若 blob 在项目根目录，请相应修改 --blob。平面腿长标定 --calibration 可继续使用，但厚度独立使用深度内参及三维平面，不由二维单应矩阵推算。

## 流水线与上传

给原有 python crab_pipeline.py 命令追加 --depth 和 --thickness-calibration depth_thickness_calibration.json，或执行 bash run_once.sh --depth --thickness-calibration depth_thickness_calibration.json。
连续运行可以直接在 crab_pipeline.py 上组合 --loop --interval 30 --depth --thickness-calibration depth_thickness_calibration.json。
把新增 crab_thickness.py、thickness_calibration.py、depth_thickness_calibration.json 与修改后的脚本一起部署到板子。
流水线会保存 runs/depth_时间戳.npz，测量 JSON 和 pipeline JSON 包含 thickness。
NPZ 包含 depth_mm 和 intrinsics，分别为对齐到识别 ROI 的原始深度与对应的 3x3 内参。
深度文件保存在本地，沿用原有 --keep 文件轮换，不会自动上传深度文件。

启用深度上传前，在 OneNET 物模型新增 thickness_ok（int32，0/1）和 thickness_mm（float，毫米）。
启用校准时，OneNET 的 thickness_mm 使用 reported_thickness_mm；只有成功测量才上报厚度，失败时上报 thickness_ok=0，不发送伪造的零厚度。
云端可能保留上一次 thickness_mm，因此必须结合 thickness_ok 判断有效性。
未启用 --depth 时不会添加这些 MQTT 属性。RS485 二进制包保持原协议，不包含厚度。

## 质量与验证

启用深度后，厚度失败会使 measurement_ok=false。thickness.reason 提供原因，例如：

- insufficient_support_depth：周围平面有效深度不足。
- support_not_planar：背景不满足单一平面条件。
- insufficient_shell_depth：背壳取样区有效深度不足。
- shell_not_separated_from_support：背壳高度未明显超过平面噪声。
- thickness_below_depth_resolution：背壳高度低于当前深度分辨率，不能可靠区分支撑平面。
- height_out_of_range：高度超出 --depth-max-height-mm（默认 150）。
- thickness_calibration_out_of_range：原始厚度超出校准配置范围，禁止外推。

默认平面内点阈值为 --depth-plane-tolerance-mm 3，背壳有效比例至少 60%，支撑平面内点比例至少 70%。
RGB、推理和深度采用时间同步，允许最大 40 毫秒差；30 秒无法取得同步帧则报错。
调试图显示背壳取样范围及所选帧高度。多帧优先选择同时满足腿部与厚度质量要求的帧，未对移动中的螃蟹做跨帧平均。

实际精度需要硬件验证：在同一放置面上用已知高度的哑光块进行测试，比较不同摆放位置及重复采样的偏差，并用卡尺核对螃蟹样本。
深度空洞、反光、遮挡和相机距离会影响可用性；不要仅调宽质量阈值来获得数字。
当前自动化测试验证合成深度的平面倾斜、缺失值、离群点及失败处理，不代表已经完成 OAK 实机精度验证。

测试命令：python -m unittest discover -s tests -v


## 无设备时可以做的工作

可以先运行软件测试和参数检查：

    python -m unittest discover -s tests -v
    python oak_crab_measure.py --help
    python crab_pipeline.py --help
    python calibrate_thickness.py --help

没有设备时可以验证算法、参数、上传队列和自动触发状态机；真实双目误差、RGB 深度对齐误差、反光表面有效率和相机掉线恢复需要设备到位后验证。

设备到位后的验证提示词：

    请在当前 Crab_Identification 项目上进行 OAK-D Lite 实机验证。
    先检查相机型号、RGB 与左右双目流、DepthAI 版本和运行日志。
    使用已知高度 10、20、30 mm 的哑光标准块，在放置区域中心及四角分别采集至少 10 次；有 50 mm 标准块时再增加该高度。
    10 mm 主要用于确认深度分辨率，不要把无效的 10 mm 结果用于校准；当前校准配置先使用 20 mm 和 30 mm。
    记录 thickness_mm、plane_rmse_mm、plane_inlier_ratio、valid_depth_ratio 和 timestamp_delta_ms。
    计算每个高度和位置的平均误差、最大绝对误差、标准差、CV、P95 误差。
    再使用至少 10 只真实螃蟹，用卡尺测量背壳高度并与程序值配对。
    确认螃蟹摆放方向、腿是否悬空、背壳 ROI 是否覆盖有效区域。
    只有误差和重复性达到项目目标后，才调整阈值并启用 OneNET thickness_mm 上报。
    失败时保存 RGB、debug 图、depth_sample.npz 和 measurement JSON，先定位失败原因，不要直接放宽质量阈值。

已保存数据可离线回放：

    python replay_depth.py --depth runs/depth_xxx.npz --measurement runs/measurement_xxx.json --output replay.json

这允许在没有 OAK 的情况下调整背壳 ROI、平面容差和最大高度参数。每轮代码、测试和设备接入注意事项记录在 DEVELOPMENT_LOG.md。
