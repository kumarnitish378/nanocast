# Copyright (c) 2026 Nitish NS <nitish.ns378@gmail.com>. All rights reserved.
# Unauthorized copying, modification, or distribution of this file is prohibited.
from transport.tcp  import TCPSender,  TCPReceiver
from transport.uart import UARTSender, UARTReceiver
from transport.udp  import UDPSender,  UDPReceiver


def get_transport(transport_type: str, mode: str, **kwargs):
    if transport_type == 'tcp':
        host = kwargs.get('host', '127.0.0.1')
        port = kwargs.get('port', 5000)
        return TCPSender(host, port) if mode == 'send' else TCPReceiver(host, port)

    if transport_type == 'uart':
        serial_port = kwargs.get('tx_port' if mode == 'send' else 'rx_port', 'COM1')
        baud        = kwargs.get('baud',   3_000_000)
        rtscts      = kwargs.get('rtscts', True)
        cls = UARTSender if mode == 'send' else UARTReceiver
        return cls(serial_port, baud, rtscts)

    if transport_type == 'udp':
        host = kwargs.get('host', '127.0.0.1')
        port = kwargs.get('port', 5000)
        return UDPSender(host, port) if mode == 'send' else UDPReceiver(host, port)

    raise ValueError(f"Unknown transport type: {transport_type!r}")
