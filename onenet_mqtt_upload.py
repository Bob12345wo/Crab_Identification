import argparse
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import quote

import paho.mqtt.client as mqtt


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


def build_properties(measurement: dict, image_name: str, image_fid: str, payload_style: str) -> dict:
    weight = measurement.get("weight") or {}
    image_upload = measurement.get("image_upload") or {}
    fid = image_fid or image_upload.get("fid") or ""
    weight_g = max(0.0, float(weight.get("weight_g") or 0.0))
    raw = {
        "weight_g": weight_g,
        "measurement_ok": 1 if measurement.get("measurement_ok") else 0,
        "image_name": image_name[:512],
        "image_fid": str(fid)[:128],
        "legs_json": compact_legs(measurement),
    }
    if payload_style == "raw":
        return raw
    if payload_style == "value":
        ts_ms = int(time.time() * 1000)
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
    image_name = Path(args.image).name if args.image else ""
    properties = build_properties(measurement, image_name, args.image_fid, config.get("payload_style", "value"))

    product_id = config["product_id"]
    device_name = config["device_name"]
    token = make_token(
        product_id=product_id,
        device_name=device_name,
        device_key=config["device_key"],
        method=config.get("token_method", "sha256"),
        expire_days=int(config.get("token_expire_days", 30)),
    )
    topic = build_topic(config)
    reply_topics = [f"{topic}/reply", f"{topic}_reply"]
    payload = {
        "id": str(int(time.time() * 1000)),
        "version": "1.0",
        "params": properties,
    }

    replies = []
    connection = {"connected": False, "rc": None}

    def on_connect(client, userdata, flags, rc):
        connection["connected"] = rc == 0
        connection["rc"] = int(rc)
        for reply_topic in reply_topics:
            client.subscribe(reply_topic, qos=int(config.get("qos", 0)))

    def on_message(client, userdata, msg):
        try:
            body = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            body = repr(msg.payload)
        replies.append({"topic": msg.topic, "payload": body})

    client = mqtt.Client(client_id=device_name, protocol=mqtt.MQTTv311)
    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(username=product_id, password=token)
    client.connect(config.get("host", "mqtts.heclouds.com"), int(config.get("port", 1883)), keepalive=60)
    client.loop_start()
    deadline = time.time() + 10
    while connection["rc"] is None and time.time() < deadline:
        time.sleep(0.1)
    if not connection["connected"]:
        raise RuntimeError(f"MQTT connect failed: {connection}")
    info = client.publish(topic, json.dumps(payload, separators=(",", ":"), ensure_ascii=False), qos=int(config.get("qos", 0)))
    info.wait_for_publish(timeout=10)
    time.sleep(2)
    client.loop_stop()
    client.disconnect()

    reply_codes = []
    for reply in replies:
        try:
            body = json.loads(reply["payload"])
            if "code" in body:
                reply_codes.append(int(body["code"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    platform_accepted = 200 in reply_codes

    result = {
        "ok": info.rc == mqtt.MQTT_ERR_SUCCESS and platform_accepted,
        "rc": int(info.rc),
        "platform_accepted": platform_accepted,
        "reply_codes": reply_codes,
        "host": config.get("host", "mqtts.heclouds.com"),
        "port": int(config.get("port", 1883)),
        "connect": connection,
        "topic": topic,
        "reply_topics": reply_topics,
        "replies": replies,
        "payload": payload,
        "properties": properties,
    }
    Path(args.result_out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
