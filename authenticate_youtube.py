import os
import subprocess
import sys

def run():
    print("=== Lammah YouTube Downloader OAuth2 Authenticator ===")
    print("This script will run yt-dlp to trigger the Google OAuth2 flow.")
    print("It will save the credentials in the persistent uploads cache directory.")
    print("-" * 55)
    
    # Resolve paths
    base_dir = os.path.dirname(os.path.abspath(__file__))
    upload_dir_env = os.getenv("UPLOAD_DIR", "uploads")
    
    # Support running from root or backend directory
    if os.path.basename(base_dir) == "app":
        base_dir = os.path.dirname(base_dir)
        
    upload_dir = upload_dir_env if os.path.isabs(upload_dir_env) else os.path.normpath(os.path.join(base_dir, upload_dir_env))
    cache_dir = os.path.join(upload_dir, "yt_dlp_cache")
    
    print(f"Cache Directory: {cache_dir}")
    os.makedirs(cache_dir, exist_ok=True)
    
    # We run yt-dlp command.
    # Note: On Windows it might be running as a python script or exe.
    # We can try running it as 'yt-dlp'
    cmd = [
        "yt-dlp",
        "--username", "oauth2",
        "--password", "",
        "--cache-dir", cache_dir,
        "--verbose",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    print("Please follow the instructions on screen (go to google.com/device and enter the code)...")
    
    try:
        # Run it interactively
        res = subprocess.run(cmd, shell=True)
        if res.returncode == 0:
            print("\n✅ SUCCESS! Authentication completed and credentials cached successfully.")
            print("Now the backend will automatically download YouTube links stably as an authenticated user!")
        else:
            print(f"\n❌ yt-dlp exited with return code {res.returncode}")
    except Exception as e:
        print(f"\n❌ Error running authentication: {e}")

if __name__ == "__main__":
    run()
