"""Read a Modbus-RTU weighing module through USB-RS485."""

import argparse
import struct
import time

import serial


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def read_regs(port: str, baud: int, slave: int, start: int, count: int) -> list[int]:
    frame = struct.pack(">B B H H", slave, 0x03, start, count)
    frame += struct.pack("<H", crc16_modbus(frame))
    with serial.Serial(port, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=1) as ser:
        ser.write(frame)
        ser.flush()
        resp = ser.read(5 + count * 2)
    if len(resp) < 5:
        raise RuntimeError(f"No response. TX={frame.hex(' ')} RX={resp.hex(' ')}")
    data = resp[:-2]
    recv_crc = struct.unpack("<H", resp[-2:])[0]
    if crc16_modbus(data) != recv_crc:
        raise RuntimeError(f"CRC error. RX={resp.hex(' ')}")
    return [struct.unpack(">H", resp[3 + i * 2:5 + i * 2])[0] for i in range(count)]


def write_reg(port: str, baud: int, slave: int, register: int, value: int) -> bytes:
    frame = struct.pack(">B B H H", slave, 0x06, register, value)
    frame += struct.pack("<H", crc16_modbus(frame))
    with serial.Serial(port, baudrate=baud, bytesize=8, parity="N", stopbits=1, timeout=1) as ser:
        ser.write(frame)
        ser.flush()
        resp = ser.read(8)
    if len(resp) < 8:
        raise RuntimeError(f"No write response. TX={frame.hex(' ')} RX={resp.hex(' ')}")
    data = resp[:-2]
    recv_crc = struct.unpack("<H", resp[-2:])[0]
    if crc16_modbus(data) != recv_crc:
        raise RuntimeError(f"Write CRC error. RX={resp.hex(' ')}")
    return resp


def regs_to_i32_ab(regs: list[int]) -> int:
    raw = (regs[0] << 16) | regs[1]
    return raw - 0x100000000 if raw & 0x80000000 else raw


def status_flags(status: int) -> dict:
    return {
        "stable": bool(status & (1 << 0)),
        "zero": bool(status & (1 << 1)),
        "overload": bool(status & (1 << 2)),
        "key_pressed": bool(status & (1 << 3)),
        "startup_no_zero": bool(status & (1 << 4)),
        "valid": bool(status & (1 << 5)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--slave", type=lambda x: int(x, 0), default=0x01)
    parser.add_argument("--register", type=lambda x: int(x, 0), default=0)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--offset", type=float, default=0.0)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--tare", action="store_true", help="Write register 4 = 1 to clear/tare the scale")
    args = parser.parse_args()

    if args.tare:
        resp = write_reg(args.port, args.baud, args.slave, 4, 1)
        print(f"tare response: {resp.hex(' ')}")

    for _ in range(args.repeat):
        regs = read_regs(args.port, args.baud, args.slave, args.register, 4)
        raw = regs_to_i32_ab(regs[:2])
        precision = regs[2]
        status = regs[3]
        weight = raw / (10 ** precision) * args.scale + args.offset
        flags = status_flags(status)
        print(
            f"regs={regs} raw_i32={raw} precision={precision} "
            f"weight={weight:.3f} status=0x{status:04x} flags={flags}"
        )
        if args.repeat > 1:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
