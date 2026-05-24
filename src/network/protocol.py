import asyncio
import json
import struct


async def send_msg(writer: asyncio.StreamWriter, msg: dict) -> None:
    data = json.dumps(msg).encode()          # dict → bytes
    header = struct.pack("!H", len(data))    # 2-byte big-endian length
    writer.write(header + data)
    await writer.drain()                     # flush the buffer


async def recv_msg(reader: asyncio.StreamReader) -> dict:
    header = await reader.readexactly(2)          # read exactly 2 bytes
    length = struct.unpack("!H", header)[0]       # decode the length
    raw = await reader.readexactly(length)         # read exactly that many bytes
    return json.loads(raw.decode())               # bytes → dict
