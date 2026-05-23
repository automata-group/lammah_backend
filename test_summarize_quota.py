import os
import sys
import asyncio
import json

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fastapi import Request
from app.api.routes.summarize import process_batch_lectures
from app.services.subscription_service import get_supabase

# Mock profile
mock_profile = {
    "id": "e676cfa9-7607-4bf7-a9a3-5c24e75bbd78", # Real or mock user ID from DB
    "full_name": "Test User",
    "quota_remaining": 500,
    "current_plan_id": "free"
}

# Mock request class
class MockRequest:
    def __init__(self):
        self.headers = {}

async def test_batch_lecture_mock():
    print("Testing batch lecture quota logic...")
    
    # We will test process_batch_lectures by mocking dependencies
    req = MockRequest()
    
    # We will invoke the event_stream directly or examine the logic
    # First, let's update a mock user profile in DB to have 5 minutes of quota
    supabase = get_supabase()
    # Find any active user
    users = supabase.table("profiles").select("*").limit(1).execute().data
    if not users:
        print("No users found in database.")
        return
        
    user = users[0]
    user_id = user["id"]
    original_quota = user["quota_remaining"]
    print(f"Testing on User ID: {user_id} | Initial Quota: {original_quota}")
    
    # Let's set quota to 10
    supabase.table("profiles").update({"quota_remaining": 10, "current_plan_id": None}).eq("id", user_id).execute()
    print("Set quota to 10 minutes (Free plan).")
    
    try:
        # Call process_batch_lectures
        # We need a couple of short youtube audio URLs or local file IDs.
        # We can use a short video URL: "https://www.youtube.com/watch?v=aqz-KE-bpKQ" (a 1 minute video)
        # We'll pass it to process_batch_lectures
        
        # Let's mock check_quota to return our user
        class MockUploadFile:
            def __init__(self, filename):
                self.filename = filename
                
        # We call the endpoint
        response = await process_batch_lectures(
            request=req,
            pdf_file=None,
            pdf_temp_id=None,
            lecture_urls="https://www.youtube.com/watch?v=aqz-KE-bpKQ,https://www.youtube.com/watch?v=aqz-KE-bpKQ",
            audio_temp_ids=None,
            profile=user
        )
        
        print("Received StreamingResponse. Reading chunks...")
        
        # Read the event stream
        async for chunk in response.body_iterator:
            if isinstance(chunk, bytes):
                chunk = chunk.decode('utf-8', errors='ignore')
            print(f"CHUNK: {chunk}")
            
    except Exception as e:
        print(f"Exception during test: {e}")
    finally:
        # Restore original quota
        supabase.table("profiles").update({"quota_remaining": original_quota}).eq("id", user_id).execute()
        print(f"Restored user's original quota to {original_quota}.")

if __name__ == "__main__":
    asyncio.run(test_batch_lecture_mock())
