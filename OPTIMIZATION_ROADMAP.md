# Crab Measurement Optimization Roadmap

| Priority | Item | Current issue | Implementation | Acceptance criterion | Status |
|---|---|---|---|---|---|
| P0 | Multi-frame selection | A single blurred or poorly positioned frame invalidates a run | Capture 5 frames and select by pose quality, reliable leg count, keypoint confidence, pose score, depth validity, plane error, and sync delta | Selected-frame metadata is saved; repeated static tests vary less than single-frame tests | Implemented |
| P0 | Per-leg reliability | One bad leg currently makes the whole result fail | Keep reliability per leg; accept a measurement when at least 8 of 10 legs are reliable | `measurement_ok` may be true with 8-9 reliable legs while bad legs remain explicitly marked | Implemented |
| P0 | Stable weight sampling | One Modbus sample may contain transient data | Read multiple samples, prefer valid and stable samples, report spread and sample count | Stable weight includes sample statistics and does not use overload/invalid samples | Implemented |
| P0 | Traceable cloud record | Image, measurement, and upload response must stay linked | Store measurement ID, model version, selected frame, image fid, and upload status | A cloud record can be traced back to one local JSON and image | Implemented |
| P0 | Static repeatability baseline | A single successful result does not quantify precision | Run 20 fixed-scene measurements and calculate reliability, CV, and range per leg | All legs reliable in at least 95% of runs, CV <= 2%, range <= 5% | Implemented |
| P1 | Offline upload queue | Network failure currently stops a run | Preserve pending jobs and retry with bounded backoff | Data captured offline is uploaded after reconnection without duplication | Implemented |
| P1 | Plane calibration | Length is currently pixels | Calibrate camera intrinsics and pixel-to-plane homography | Reference lengths across the working area are within the agreed mm error | Blocked by calibration target |
| P1 | Thickness measurement | 2D pose cannot provide crab thickness | Calibrate stereo depth and use robust body ROI depth statistics | Thickness error is validated against caliper measurements | Blocked by real samples |
| P1 | Endpoint refinement | Leg tips dominate length error | Build a labeled endpoint validation set, then use ROI refinement or retraining | Endpoint error and per-leg length error improve on a held-out real-crab set | Blocked by labeled data |
| P2 | Automatic trigger | Manual execution is inefficient | Trigger only after weight is stable and above threshold; add cooldown | One crab produces one record without duplicate uploads | Implemented |
| P2 | Service hardening | Manual shell operation and SD-card risk | Add systemd service, rotating logs, health checks, and controlled shutdown | Reboots recover automatically and 24-hour soak test has no I/O errors | Planned |

## Measurement acceptance

- Overall acceptance never replaces per-leg reliability.
- Consumers must ignore any leg whose `reliable` field is false.
- Pixel values are diagnostic only until a calibration file is active.
- Production accuracy must be validated against manually measured real crabs.
