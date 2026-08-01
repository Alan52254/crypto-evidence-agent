import asyncio, httpx, base64, os, json
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

async def test():
    key = os.environ.get("GEMINI_API_KEY", "")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get("https://fred.stlouisfed.org/graph/fredgraph.png?g=1AAAA&width=880&height=440", follow_redirects=True)
        print("Image: " + str(len(r.content)//1024) + "KB")
        img_b64 = base64.b64encode(r.content).decode()
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=" + key
        payload = {"contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": "image/png", "data": img_b64}}, {"text": "Describe this chart briefly. What is the title, axes, latest value, and trend?"}]}]}
        resp = await client.post(url, json=payload)
        print("Status: " + str(resp.status_code))
        if resp.status_code == 200:
            body = resp.json()
            text = body["candidates"][0]["content"]["parts"][0]["text"]
            print("Result: " + text[:500])
        else:
            print("Error: " + resp.text[:300])

asyncio.run(test())
