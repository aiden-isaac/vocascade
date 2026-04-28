import asyncio
import aiohttp

async def main():
    text = "Hello, testing the genie TTS API!"
    url = "http://127.0.0.1:9880/"
    params = {
        "text": text,
        "text_language": "en"
    }
    try:
        async with aiohttp.ClientSession() as session:
            print(f"Making request to {url} with params {params}")
            async with session.get(url, params=params) as response:
                print(f"Status: {response.status}")
                if response.status == 200:
                    chunks = 0
                    async for chunk in response.content.iter_chunked(4096):
                        chunks += 1
                        if chunks == 1:
                            print(f"Received first chunk of size {len(chunk)}")
                    print(f"Successfully received {chunks} audio chunks.")
                else:
                    text = await response.text()
                    print(f"Error body: {text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
