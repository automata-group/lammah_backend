import uvicorn
import os
import sys

# Get the absolute path of the directory containing this script (the backend folder)
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

if __name__ == "__main__":
    # Import the app after setting the path
    from app.main import app
    print(f"Lammah Backend starting from: {current_dir}")
    uvicorn.run(app, host="0.0.0.0", port=8003)
