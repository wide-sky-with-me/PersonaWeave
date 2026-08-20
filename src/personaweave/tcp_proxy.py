import asyncio
import os


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    upstream_host = os.environ.get("PERSONAWEAVE_PROXY_UPSTREAM_HOST", "172.30.0.1")
    upstream_port = int(os.environ.get("PERSONAWEAVE_PROXY_UPSTREAM_PORT", "8110"))
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(
            upstream_host,
            upstream_port,
        )
    except OSError:
        writer.close()
        await writer.wait_closed()
        return
    await asyncio.gather(
        _pipe(reader, upstream_writer),
        _pipe(upstream_reader, writer),
        return_exceptions=True,
    )


async def main() -> None:
    listen_port = int(os.environ.get("PERSONAWEAVE_PROXY_LISTEN_PORT", "8000"))
    server = await asyncio.start_server(_handle_client, "0.0.0.0", listen_port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
