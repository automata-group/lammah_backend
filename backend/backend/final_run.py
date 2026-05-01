
import uvicorn
import os
import sys

# Ensure backend directory is in path
sys.path.append(os.getcwd())

if __name__ == "__main__":
    from app.main import app
    print("READY_TO_START")
    uvicorn.run(app, host="0.0.0.0", port=8003)
