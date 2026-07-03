#!/usr/bin/env python3
"""TCP port forwarder — listens on 0.0.0.0:<listen_port> and forwards to 127.0.0.1:<target_port>.

Designed for HA addon sidecar use: started as a background process by the s6 run script,
it creates an external-facing listener so that Docker port-mapping works even when the
target service binds only to 127.0.0.1.

Usage:
    python3 tcp_proxy.py --listen-port 14096 --target-port 14095
"""
import argparse
import asyncio
import signal


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Read from *reader* and write to *writer* until EOF or error."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    """Safely flush and close a stream writer."""
    try:
        writer.close()
        await writer.wait_closed()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
    client_id: int,
) -> None:
    """Bidirectionally bridge one client connection to the target server."""
    # Connect to the target (inner) server
    try:
        target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
    except (ConnectionRefusedError, OSError) as exc:
        print(f"[TCP Proxy] #{client_id} Connection to {target_host}:{target_port} failed: {exc}")
        await _close_writer(client_writer)
        return

    peername = client_writer.get_extra_info("peername", ("?", 0))
    print(
        f"[TCP Proxy] #{client_id} Connected: "
        f"{peername[0]}:{peername[1]} \u2192 {target_host}:{target_port}"
    )

    try:
        # Bidirectional forwarding: both directions run concurrently
        await asyncio.gather(
            _relay(client_reader, target_writer),
            _relay(target_reader, client_writer),
        )
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        await _close_writer(client_writer)
        await _close_writer(target_writer)
        print(f"[TCP Proxy] #{client_id} Disconnected")


async def main() -> None:
    """Parse args, start the TCP proxy server, and wait for shutdown signal."""
    parser = argparse.ArgumentParser(
        description="TCP port forwarder — expose a local-only port via a public listener."
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        required=True,
        help="External port to listen on (e.g. 14096, the mapped Docker port)",
    )
    parser.add_argument(
        "--target-port",
        type=int,
        required=True,
        help="Internal port to forward to (e.g. 14095, where mimo serve listens)",
    )
    parser.add_argument(
        "--target-host",
        default="127.0.0.1",
        help="Target host address (default: 127.0.0.1)",
    )
    args = parser.parse_args()

    client_counter: int = 0

    async def on_connect(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        nonlocal client_counter
        client_counter += 1
        await handle_client(
            reader, writer, args.target_host, args.target_port, client_counter
        )

    server = await asyncio.start_server(
        on_connect,
        host="0.0.0.0",
        port=args.listen_port,
    )

    print(
        f"[TCP Proxy] Listening on 0.0.0.0:{args.listen_port} "
        f"\u2192 {args.target_host}:{args.target_port}"
    )

    # Graceful shutdown on SIGTERM / SIGINT
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        print("[TCP Proxy] Shutdown signal received, closing server...")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except (NotImplementedError, AttributeError):
            pass

    async with server:
        await stop_event.wait()

    server.close()
    await server.wait_closed()
    print("[TCP Proxy] Bye")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
