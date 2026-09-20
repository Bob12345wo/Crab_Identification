import argparse
import base64
import hashlib
import hmac
import json
import time
import threading
from pathlib import Path
from urllib.parse import quote

import paho.mqtt.client as mqtt

from upload_queue import write_json


LEG_KEYS = {
    "L-Claw": "LC",
    "L-Tleg": "LT",
    "L-Aleg": "LA",
    "L-Bleg": "LB",
    "L-Cleg": "LCG",
    "R-Claw": "RC",
    "R-Tleg": "RT",
    "R-Aleg": "RA",
    "R-Bleg": "RB",
    "R-Cleg": "RCG",
}


def hmac_digest(method: str):
    methods = {
        "md5": hashlib.md5,
        "sha1": hashlib.sha1,
        "sha256": hashlib.sha256,
    }
    if method not in methods:
        raise ValueError(f"Unsupported token method: {method}")
    return methods[method]


def make_token(product_id: str, device_name: str, device_key: str, method: str, expire_days: int) -> str:
    version = "2018-10-31"
    et = str(int(time.time()) + int(expire_days) * 24 * 3600)
    res = f"products/{product_id}/devices/{device_name}"
    sign_content = f"{et}\n{method}\n{res}\n{version}"
    try:
        key = base64.b64decode(device_key)
    except Exception:
        key = device_key.encode("utf-8")
    sign = base64.b64encode(hmac.new(key, sign_content.encode("utf-8"), hmac_digest(method)).digest()).decode()
    return (
        f"version={version}"
        f"&res={quote(res, safe='')}"
        f"&et={et}"
        f"&method={method}"
        f"&sign={quote(sign, safe='')}"
    )


def compact_legs(measurement: dict) -> str:
    out = {
        "id": str(measurement.get("measurement_id") or "")[:12],
        "u": str(measurement.get("unit") or "px"),
        "n": int(measurement.get("reliable_leg_count") or 0),
    }
    bad = [LEG_KEYS.get(name, name) for name in measurement.get("unreliable_legs", [])]
    if bad:
        out["bad"] = ",".join(bad)
    for row in measurement.get("legs", []):
        key = LEG_KEYS.get(row.get("leg"), row.get("leg", "unknown"))
        value = row.get("total_mm")
        if value is None:
            value = row.get("total_px")
        out[key] = round(float(value or 0), 2)
        out[f"{key}r"] = 1 if row.get("reliable") else 0
    text = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    return text[:512]


def build_properties(measurement: dict, image_name: str, image_fid: str, payload_style: str,
                     include_weight_status: bool = False) -> dict:
    weight = measurement.get("weight") or {}
    image_upload = measurement.get("image_upload") or {}
    fid = image_fid or image_upload.get("fid") or ""
    weight_ok = bool(weight and weight.get("ok", weight.get("valid", True) and not weight.get("overload", False))
                     and weight.get("weight_g") is not None)
    raw = {
        "measurement_ok": 1 if measurement.get("measurement_ok") and (not weight or weight_ok) else 0,
        "image_name": image_name[:512],
        "image_fid": str(fid)[:128],
        "legs_json": compact_legs(measurement),
    }
    if weight_ok:
        raw["weight_g"] = max(0.0, float(weight["weight_g"]))
    if include_weight_status:
        raw["weight_ok"] = int(weight_ok)
    thickness = measurement.get("thickness")
    if thickness is not None:
        raw["thickness_ok"] = 1 if thickness.get("ok") else 0
        reported = thickness.get("reported_thickness_mm", thickness.get("thickness_mm"))
        if thickness.get("ok") and reported is not None:
            raw["thickness_mm"] = round(float(reported), 2)
    if payload_style == "raw":
        return raw
    if payload_style == "value":
        ts_ms = int(float(measurement.get("timestamp") or time.time()) * 1000)
        return {key: {"value": value, "time": ts_ms} for key, value in raw.items()}
    raise ValueError(f"Unsupported payload_style: {payload_style}")


def build_topic(config: dict) -> str:
    product_id = config["product_id"]
    device_name = config["device_name"]
    if config.get("topic_property_post"):
        return config["topic_property_post"].format(product_id=product_id, device_name=device_name)
    mode = config.get("topic_mode", "property")
    if mode == "property":
        suffix = "thing/property/post"
    elif mode == "event_property":
        suffix = "thing/event/property/post"
    else:
        raise ValueError(f"Unsupported topic_mode: {mode}")
    return f"$sys/{product_id}/{device_name}/{suffix}"


def publish_measurement(config, measurement, image_name, image_fid, client_factory=None):
    measurement_id = str(measurement["measurement_id"])
    properties = build_properties(measurement, image_name, image_fid, config.get("payload_style", "value"),
                                  bool(config.get("include_weight_status", False)))
    image_fid = str(image_fid or (measurement.get("image_upload") or {}).get("fid") or "")
    topic = build_topic(config)
    reply_topics = [f"{topic}/reply", f"{topic}_reply"]
    # Retries of the same record/image pair reuse the request ID.
    request_id = str(int(hashlib.sha256(f"{measurement_id}|{image_fid}".encode()).hexdigest()[:15], 16))
    payload = {"id": request_id, "version": "1.0", "params": properties}
    result = {"ok": False, "measurement_id": measurement_id, "image_fid": image_fid,
              "platform_accepted": False, "reply_codes": [], "replies": [],
              "topic": topic, "reply_topics": reply_topics, "payload": payload, "properties": properties}
    connected, subscribed, replied = threading.Event(), threading.Event(), threading.Event()
    connection = {"connected": False, "rc": None, "subscribed": False}

    def on_connect(client, userdata, flags, rc):
        connection.update(connected=rc == 0, rc=int(rc))
        connected.set()
        if rc == 0:
            client.subscribe([(reply_topic, int(config.get("qos", 0))) for reply_topic in reply_topics])

    def on_subscribe(client, userdata, mid, granted_qos):
        connection["subscribed"] = bool(granted_qos) and all(int(code) < 128 for code in granted_qos)
        subscribed.set()

    def on_message(client, userdata, msg):
        if msg.topic not in reply_topics:
            return
        try:
            body = json.loads(msg.payload.decode("utf-8"))
            if not isinstance(body, dict) or str(body.get("id")) != request_id or "code" not in body:
                return
            code = int(body["code"])
        except (UnicodeError, ValueError, TypeError):
            return
        if replied.is_set():
            return
        result["reply_codes"].append(code)
        result["replies"].append({"topic": msg.topic, "payload": json.dumps(body)})
        result["platform_accepted"] = code == 200
        replied.set()

    client = None
    try:
        timeout = float(config.get("mqtt_timeout", 30))
        if not 0 < timeout < float("inf"):
            raise ValueError("mqtt_timeout must be finite and positive")
        deadline = time.monotonic() + timeout

        def remaining():
            return max(0.0, deadline - time.monotonic())

        client = (client_factory or mqtt.Client)(client_id=config["device_name"], protocol=mqtt.MQTTv311)
        client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
        client.connect_timeout = min(timeout, 10.0)
        token = make_token(config["product_id"], config["device_name"], config["device_key"],
                           config.get("token_method", "sha256"), int(config.get("token_expire_days", 30)))
        client.username_pw_set(username=config["product_id"], password=token)
        client.connect(config.get("host", "mqtts.heclouds.com"), int(config.get("port", 1883)), keepalive=60)
        client.loop_start()
        if not connected.wait(remaining()) or not connection["connected"]:
            raise RuntimeError(f"MQTT connection failed: {connection}")
        if not subscribed.wait(remaining()) or not connection["subscribed"]:
            raise RuntimeError("MQTT reply subscription not acknowledged")
        info = client.publish(topic, json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                              qos=int(config.get("qos", 0)))
        result["rc"] = int(info.rc)
        info.wait_for_publish(timeout=remaining())
        if info.rc != mqtt.MQTT_ERR_SUCCESS or not info.is_published():
            raise RuntimeError("MQTT message was not published before timeout")
        if not replied.wait(remaining()):
            raise RuntimeError("Timed out waiting for matching MQTT reply ID")
        result["ok"] = bool(result["platform_accepted"])
        if not result["ok"]:
            result["error"] = "Platform rejected this request"
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        result["connect"] = connection
        if client is not None:
            try:
                client.disconnect()
            finally:
                client.loop_stop()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="onenet_mqtt_config.json")
    parser.add_argument("--measurement", required=True)
    parser.add_argument("--image", default="")
    parser.add_argument("--image-fid", default="")
    parser.add_argument("--result-out", default="onenet_mqtt_result.json")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    measurement = json.loads(Path(args.measurement).read_text(encoding="utf-8"))
    result = publish_measurement(config, measurement, Path(args.image).name if args.image else "", args.image_fid)
    write_json(args.result_out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
