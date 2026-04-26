
import os
import sys
import uvicorn

print("Python initialized")
print(f"Python path: {sys.path}")
print("Importing app.main...")
try:
    from app.main import app
    print("Import successful")
    print("Starting uvicorn...")
    uvicorn.run(app, host="0.0.0.0", port=8003)
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
