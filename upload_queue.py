"""Durable upload checkpoints and retention of completed measurement groups."""

import json
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


@contextmanager
def output_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".pipeline.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"Another pipeline is using {directory.resolve()}") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def artifact_path(value, directory):
    """Only act on files in the job directory, including relocated legacy jobs."""
    directory = Path(directory).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = directory / path.name
    if path.resolve().parent != directory:
        raise ValueError(f"Artifact outside job directory: {path}")
    return path


def result_or_empty(path):
    try:
        data = read_json(path)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def job_artifacts(job, directory, stamp):
    values = list(job.get("files", []))
    values += [job[key] for key in ("measurement", "image", "file_upload_result", "mqtt_upload_result")
               if job.get(key)]
    # Older pending records did not enumerate debug/depth files.
    values += [f"{prefix}_{stamp}.{suffix}" for prefix, suffix in
               (("measurement", "json"), ("raw", "jpg"), ("debug", "jpg"), ("depth", "npz"),
                ("onenet_file", "json"), ("onenet_upload", "json"))]
    return {artifact_path(value, directory) for value in values}


def rotate_outputs(out_dir, keep):
    """Keep N completed measurements; pending, malformed and orphan files stay."""
    if keep <= 0:
        return
    directory = Path(out_dir).resolve()
    protected = set()
    for pending in directory.glob("pending_*.json"):
        try:
            protected |= job_artifacts(read_json(pending), directory, pending.stem[len("pending_"):])
        except (OSError, ValueError, TypeError, KeyError):
            # A corrupt manifest may be the only reference to unuploaded data.
            return
    completed = []
    for manifest in directory.glob("pipeline_*.json"):
        stamp = manifest.stem[len("pipeline_"):]
        if (directory / f"pending_{stamp}.json").exists():
            continue
        job = result_or_empty(manifest)
        if not (job.get("file_upload_ok") and job.get("mqtt_upload_ok")):
            continue
        try:
            files = job_artifacts(job, directory, stamp) | {manifest}
        except (ValueError, TypeError):
            continue
        if not files & protected:
            completed.append((manifest.stat().st_mtime, files))
    completed.sort(key=lambda row: row[0], reverse=True)
    for _, files in completed[keep:]:
        for path in files:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                print(f"warn: retention could not remove {path}: {exc}", file=sys.stderr)


def attempt_upload(pending, args, runner, force=False):
    pending = Path(pending).resolve()
    directory = pending.parent
    job = read_json(pending)
    if not force and float(job.get("next_retry_at", 0)) > time.time():
        return False
    stamp = pending.stem[len("pending_"):]
    measurement = artifact_path(job["measurement"], directory)
    image = artifact_path(job["image"], directory)
    file_output = artifact_path(job.get("file_upload_result") or f"onenet_file_{stamp}.json", directory)
    mqtt_output = artifact_path(job.get("mqtt_upload_result") or f"onenet_upload_{stamp}.json", directory)
    manifest = directory / f"pipeline_{stamp}.json"
    job.update(file_upload_result=str(file_output), mqtt_upload_result=str(mqtt_output))
    job["attempts"] = int(job.get("attempts", 0)) + 1
    job["last_attempt_at"] = time.time()
    job["next_retry_at"] = time.time() + min(args.retry_max_delay,
                                             args.retry_base_delay * 2 ** min(job["attempts"] - 1, 16))
    write_json(pending, job)
    try:
        data = read_json(measurement)
        measurement_id = str(data["measurement_id"])
        job["measurement_id"] = measurement_id
        config = str(job.get("config") or args.config)
        if not image.is_file():
            raise RuntimeError(f"Missing image: {image}")
        if not job.get("file_upload_ok"):
            if job.get("skip_file_upload"):
                job["file_upload_ok"] = True
            else:
                result = result_or_empty(file_output)
                # Recover a confirmed file upload after an interrupted checkpoint.
                confirmed = (result.get("ok") and result.get("fid")
                             and Path(result.get("image", "")).resolve() == image)
                if not confirmed:
                    file_output.unlink(missing_ok=True)
                    runner([sys.executable, "onenet_file_upload.py", "--config", config,
                            "--image", str(image), "--result-out", str(file_output)], timeout=args.upload_timeout)
                    result = read_json(file_output)
                if not result.get("ok") or not result.get("fid"):
                    raise RuntimeError("File upload not confirmed with a file ID")
                job.update(file_upload_ok=True, image_fid=str(result["fid"]))
                # Properties must be reposted if their previous image ID was empty.
                job["mqtt_upload_ok"] = False
            write_json(pending, job)
        data["image_upload"] = {"ok": bool(job.get("image_fid")), "fid": job.get("image_fid", ""),
                                "skipped": bool(job.get("skip_file_upload")), "image_name": image.name,
                                "result_file": str(file_output)}
        write_json(measurement, data)
        if not job.get("mqtt_upload_ok"):
            result = result_or_empty(mqtt_output)
            confirmed = (result.get("ok") and result.get("measurement_id") == measurement_id
                         and result.get("image_fid", "") == job.get("image_fid", ""))
            if not confirmed:
                mqtt_output.unlink(missing_ok=True)
                runner([sys.executable, "onenet_mqtt_upload.py", "--config", config,
                        "--measurement", str(measurement), "--image", str(image),
                        "--image-fid", job.get("image_fid", ""), "--result-out", str(mqtt_output)],
                       timeout=args.upload_timeout)
                result = read_json(mqtt_output)
            if not (result.get("ok") and result.get("measurement_id") == measurement_id
                    and result.get("image_fid", "") == job.get("image_fid", "")):
                raise RuntimeError("Matching MQTT acknowledgement not received")
            job["mqtt_upload_ok"] = True
            write_json(pending, job)
        job.update(last_error=None, completed_at=time.time(), next_retry_at=0)
        write_json(manifest, job)
        pending.unlink()
        return True
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        job["last_error"] = str(exc)
        write_json(pending, job)
        write_json(manifest, job)
        print(f"pending upload: {pending.name}: {exc}", file=sys.stderr, flush=True)
        return False


def retry_pending(args, runner, force=False):
    attempted = 0
    for pending in sorted(Path(args.out_dir).glob("pending_*.json")):
        if attempted >= args.retry_limit:
            break
        try:
            job = read_json(pending)
            if not force and float(job.get("next_retry_at", 0)) > time.time():
                continue
            attempted += 1
            attempt_upload(pending, args, runner, force=force)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"warn: preserving unreadable pending job {pending}: {exc}", file=sys.stderr)
    return attempted
