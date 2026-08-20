import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import time
import uuid
from pathlib import Path
from urllib import request
from urllib.parse import quote, quote_plus


def hmac_digest(method: str):
    methods = {
        "md5": hashlib.md5,
        "sha1": hashlib.sha1,
        "sha256": hashlib.sha256,
    }
    if method not in methods:
        raise ValueError(f"Unsupported token method: {method}")
    return methods[method]


def make_token(
    res: str,
    access_key: str,
    method: str,
    expire_days: int,
    version: str = "2022-05-01",
    key_encoding: str = "auto",
    url_encoding: str = "quote",
) -> str:
    et = str(int(time.time()) + int(expire_days) * 24 * 3600)
    sign_content = f"{et}\n{method}\n{res}\n{version}"
    if key_encoding == "raw":
        key = access_key.encode("utf-8")
    elif key_encoding == "hex":
        key = bytes.fromhex(access_key)
    elif key_encoding == "base64":
        key = base64.b64decode(access_key)
    elif key_encoding == "auto":
        try:
            key = base64.b64decode(access_key, validate=True)
        except Exception:
            key = access_key.encode("utf-8")
    else:
        raise ValueError(f"Unsupported key encoding: {key_encoding}")
    sign = base64.b64encode(hmac.new(key, sign_content.encode("utf-8"), hmac_digest(method)).digest()).decode()
    encode = quote_plus if url_encoding == "quote_plus" else quote
    return (
        f"version={version}"
        f"&res={encode(res, safe='')}"
        f"&et={et}"
        f"&method={method}"
        f"&sign={encode(sign, safe='')}"
    )


def build_multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = "----crab-oak-" + uuid.uuid4().hex
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")

    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload_file(config: dict, image_path: Path) -> dict:
    product_id = config["product_id"]
    device_name = config["device_name"]
    res = config.get("file_auth_res") or f"products/{product_id}"
    access_key = (
        config.get("file_access_key")
        or config.get("user_access_key")
        or config.get("access_key")
        or config.get("product_access_key")
        or ""
    )
    token = config.get("file_authorization")
    if not token:
        if not access_key:
            raise ValueError(
                "File upload needs file_authorization or file_access_key in onenet_mqtt_config.json."
            )
        token = make_token(
            res=res,
            access_key=access_key,
            method=config.get("file_token_method", "sha1"),
            expire_days=int(config.get("file_token_expire_days", config.get("token_expire_days", 30))),
            version=config.get("file_token_version", "2022-05-01"),
            key_encoding=config.get("file_key_encoding", "auto"),
            url_encoding=config.get("file_url_encoding", "quote"),
        )
    body, content_type = build_multipart(
        {"product_id": product_id, "device_name": device_name},
        "file",
        image_path,
    )
    url = config.get("file_upload_url", "https://iot-api.heclouds.com/device/file-upload")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            config.get("file_auth_header", "Authorization"): token,
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
        },
    )
    started = time.time()
    try:
        with request.urlopen(req, timeout=float(config.get("file_upload_timeout", 30))) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "image": str(image_path),
            "error": repr(exc),
            "elapsed_s": round(time.time() - started, 3),
        }

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    code = None if not isinstance(parsed, dict) else parsed.get("code")
    fid = None if not isinstance(parsed, dict) else (parsed.get("data") or {}).get("fid")
    return {
        "ok": 200 <= status < 300 and code == 0,
        "url": url,
        "auth_res": res,
        "auth_version": config.get("file_token_version", "2022-05-01"),
        "auth_method": config.get("file_token_method", "sha1"),
        "key_encoding": config.get("file_key_encoding", "auto"),
        "url_encoding": config.get("file_url_encoding", "quote"),
        "authorization_override": bool(config.get("file_authorization")),
        "access_key_present": bool(access_key),
        "access_key_prefix": access_key[:4] if access_key else "",
        "access_key_suffix": access_key[-4:] if access_key else "",
        "status": status,
        "code": code,
        "fid": fid,
        "image": str(image_path),
        "response": parsed if parsed is not None else text,
        "elapsed_s": round(time.time() - started, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload one image to OneNET file management.")
    parser.add_argument("--config", default="onenet_mqtt_config.json")
    parser.add_argument("--image", required=True)
    parser.add_argument("--result-out", default="onenet_file_upload_result.json")
    parser.add_argument("--auth-res", default=None, help="Override authorization resource, e.g. userid/xxx")
    parser.add_argument("--access-key-field", default=None, help="Config key to use as access key")
    parser.add_argument("--key-encoding", default=None, choices=["auto", "raw", "hex", "base64"])
    parser.add_argument("--auth-header", default=None, choices=["Authorization", "authorization"])
    parser.add_argument("--token-method", default=None, choices=["md5", "sha1", "sha256"])
    parser.add_argument("--java-url-encoding", action="store_true", help="Use Java URLEncoder-style quote_plus")
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.auth_res:
        config["file_auth_res"] = args.auth_res
    if args.access_key_field:
        value = config.get(args.access_key_field)
        if not value:
            raise ValueError(f"Missing config field: {args.access_key_field}")
        config["file_access_key"] = value
    if args.key_encoding:
        config["file_key_encoding"] = args.key_encoding
    if args.auth_header:
        config["file_auth_header"] = args.auth_header
    if args.token_method:
        config["file_token_method"] = args.token_method
    if args.java_url_encoding:
        config["file_url_encoding"] = "quote_plus"
    result = upload_file(config, Path(args.image))
    Path(args.result_out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
