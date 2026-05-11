import asyncio
import aiohttp
import sys

async def main():
    text = "Hello! Testing the local TTS generation!"
    url = "http://127.0.0.1:9880/"
    # Based on the api.py usage we saw: -dr 123.wav -dt 一二三。 -dl zh
    # Wait, the wrapper doesn't seem to be running on 9880. Let's find where it is or why it crashed.
    pass

if __name__ == "__main__":
    asyncio.run(main())
