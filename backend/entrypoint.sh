#!/bin/sh

# Function to evaluate health (placeholder for more complex logic if needed)
check_health() {
    echo "Checking service health..."
    # We can use curl to check if the port is responding
    curl -f http://localhost:${PORT:-8000}/api/v1/health || exit 1
}

echo "Starting Backend Application..."

# Try to run the application
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}

# If the command above fails (exits with non-zero), we don't want the container to die
if [ $? -ne 0 ]; then
    echo "ERROR: Application failed to start!"
    echo "Container will remain active for debugging. Press CTRL+C to stop manually."
    # Infinite loop to keep container alive
    while true; do sleep 1000; done
fi
