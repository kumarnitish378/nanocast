#!/usr/bin/env python3
"""
UART diagnostic script - run before starting the video pipeline.

Tests
-----
1. list       - show all available COM ports
2. loopback   - single-port TX->RX loopback (short TX pin to RX pin on the adapter)
3. link       - two-port cross-cable test (TX of port-A -> RX of port-B and back)

Usage
-----
python uart_diag.py list
python uart_diag.py loopback COM7 --baud 230400
python uart_diag.py link COM7 COM8 --baud 230400
"""

import argparse
import sys
import time
import threading

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip install pyserial")
    sys.exit(1)


PASS = "[PASS]"
FAIL = "[FAIL]"
INFO = "[INFO]"


# -- List ports ----------------------------------------------------------------

def cmd_list(_args):
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print(f"{INFO} No serial ports found.")
        return
    print(f"{INFO} Available serial ports:\n")
    for p in sorted(ports, key=lambda x: x.device):
        print(f"  {p.device:<12} {p.description}")
    print()


# -- Loopback test -------------------------------------------------------------

def cmd_loopback(args):
    port = args.port
    baud = args.baud
    print(f"\n{'-'*50}")
    print(f"Loopback test on {port} @ {baud:,} baud")
    print(f"  Make sure TX pin is shorted to RX pin on {port}.")
    print(f"{'-'*50}\n")

    try:
        ser = serial.Serial(port, baud, timeout=1.0, write_timeout=2.0,
                            rtscts=False)
    except serial.SerialException as e:
        print(f"{FAIL} Could not open {port}: {e}")
        sys.exit(1)

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    payload = b'\xAA\x55\x01\x02\x03\xDE\xAD\xBE\xEF'
    failures = 0

    for i in range(5):
        ser.write(payload)
        time.sleep(0.05)
        received = ser.read(len(payload))
        if received == payload:
            print(f"  Round {i+1}: {PASS}  sent={payload.hex()}  recv={received.hex()}")
        else:
            failures += 1
            print(f"  Round {i+1}: {FAIL}  sent={payload.hex()}  recv={received.hex() or '(nothing)'}")

    ser.close()
    print()
    if failures == 0:
        print(f"{PASS} Loopback OK — {port} TX and RX are working.\n")
    elif failures == 5:
        print(f"{FAIL} No data returned.")
        print("      -> Is TX shorted to RX on the adapter?")
        print("      -> Is this the correct COM port?\n")
    else:
        print(f"{FAIL} {failures}/5 rounds failed — possible wiring or baud mismatch.\n")


# -- Two-port link test --------------------------------------------------------

def cmd_link(args):
    port_a = args.port_a
    port_b = args.port_b
    baud   = args.baud

    print(f"\n{'-'*50}")
    print(f"Cross-cable link test")
    print(f"  {port_a}  TX -> RX  {port_b}")
    print(f"  {port_a}  RX <- TX  {port_b}")
    print(f"  Baud: {baud:,}")
    print(f"{'-'*50}\n")

    try:
        ser_a = serial.Serial(port_a, baud, timeout=1.0, write_timeout=2.0,
                              rtscts=False)
        ser_b = serial.Serial(port_b, baud, timeout=1.0, write_timeout=2.0,
                              rtscts=False)
    except serial.SerialException as e:
        print(f"{FAIL} Could not open port: {e}\n")
        sys.exit(1)

    ser_a.reset_input_buffer(); ser_a.reset_output_buffer()
    ser_b.reset_input_buffer(); ser_b.reset_output_buffer()

    results = {}

    def _test(label, tx_ser, rx_ser, payload):
        tx_ser.write(payload)
        time.sleep(0.1)
        received = rx_ser.read(len(payload))
        ok = received == payload
        results[label] = ok
        status = PASS if ok else FAIL
        print(f"  {label}: {status}  sent={payload.hex()}  recv={received.hex() or '(nothing)'}")

    payload_ab = b'\xAA\x55\x11\x22\x33'
    payload_ba = b'\xAA\x55\x44\x55\x66'

    print(f"  {port_a} -> {port_b}:")
    _test(f"    {port_a}->{port_b} round 1", ser_a, ser_b, payload_ab)
    _test(f"    {port_a}->{port_b} round 2", ser_a, ser_b, payload_ab[::-1])

    print(f"\n  {port_b} -> {port_a}:")
    _test(f"    {port_b}->{port_a} round 1", ser_b, ser_a, payload_ba)
    _test(f"    {port_b}->{port_a} round 2", ser_b, ser_a, payload_ba[::-1])

    ser_a.close()
    ser_b.close()

    passed = sum(results.values())
    total  = len(results)
    print()

    if passed == total:
        print(f"{PASS} Link OK — both directions working. Ready for video.\n")
    elif passed == 0:
        print(f"{FAIL} No data in either direction.")
        print("      -> Check TX/RX cross-wiring (TX of one -> RX of the other).")
        print("      -> Check GND is connected.")
        print(f"      -> Are {port_a} and {port_b} the correct ports?\n")
    else:
        ab = all(v for k, v in results.items() if f'{port_a}->' in k)
        ba = all(v for k, v in results.items() if f'{port_b}->' in k)
        if ab and not ba:
            print(f"{FAIL} One-way only ({port_a}->{port_b} OK, {port_b}->{port_a} FAIL).")
            print(f"      -> Check the TX wire from {port_b} to RX of {port_a}.\n")
        elif ba and not ab:
            print(f"{FAIL} One-way only ({port_b}->{port_a} OK, {port_a}->{port_b} FAIL).")
            print(f"      -> Check the TX wire from {port_a} to RX of {port_b}.\n")
        else:
            print(f"{FAIL} {passed}/{total} tests passed — intermittent. Check connections.\n")


# -- Entry point ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='UART diagnostic tool')
    sub    = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('list', help='List all available COM ports')

    p_lb = sub.add_parser('loopback', help='Single-port TX->RX loopback test')
    p_lb.add_argument('port',          help='COM port  e.g. COM7')
    p_lb.add_argument('--baud', type=int, default=230400)

    p_lk = sub.add_parser('link', help='Two-port cross-cable test')
    p_lk.add_argument('port_a',        help='First port  e.g. COM7')
    p_lk.add_argument('port_b',        help='Second port e.g. COM8')
    p_lk.add_argument('--baud', type=int, default=230400)

    args = parser.parse_args()
    {'list': cmd_list, 'loopback': cmd_loopback, 'link': cmd_link}[args.cmd](args)


if __name__ == '__main__':
    main()
