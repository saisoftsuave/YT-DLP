#!/usr/bin/env python3
\"\"\"
Test script for the Social Media Downloader API
\"\"\"
import httpx
import asyncio

async def test_api():
    base_url = \"http://localhost:8000\"  # Change this to your Render URL when deployed
    
    try:
        # Test health endpoint
        async with httpx.AsyncClient() as client:
            print(\"Testing health endpoint...\")
            response = await client.get(f\"{base_url}/api/health\")
            print(f\"Health check: {response.status_code} - {response.json()}\")
            
            print(\"\\nTesting root endpoint...\")
            response = await client.get(f\"{base_url}/\")
            print(f\"Root endpoint: {response.status_code}\")
            print(f\"Response: {response.json()}\")
            
            print(\"\\nAPI is working correctly!\")
            
    except Exception as e:
        print(f\"Error testing API: {e}\")

if __name__ == \"__main__\":
    asyncio.run(test_api())