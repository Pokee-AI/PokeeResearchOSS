#!/bin/bash

# Start script for Wazir Ahmed International L.L.C Website
# This script starts a simple web server to view the website

echo "================================================"
echo "Wazir Ahmed International L.L.C Website"
echo "================================================"
echo ""

# Check if we're in the correct directory
if [ ! -f "index.html" ]; then
    echo "Error: index.html not found!"
    echo "Please run this script from the website directory:"
    echo "cd /vercel/sandbox/website && ./start.sh"
    exit 1
fi

# Try different methods to start a web server
echo "Starting web server..."
echo ""

# Method 1: Try Python 3
if command -v python3 &> /dev/null; then
    echo "Using Python 3 HTTP server on port 8080"
    echo "Open your browser to: http://localhost:8080"
    echo ""
    echo "Press Ctrl+C to stop the server"
    echo "================================================"
    python3 -m http.server 8080
    exit 0
fi

# Method 2: Try Python 2
if command -v python &> /dev/null; then
    echo "Using Python 2 HTTP server on port 8080"
    echo "Open your browser to: http://localhost:8080"
    echo ""
    echo "Press Ctrl+C to stop the server"
    echo "================================================"
    python -m SimpleHTTPServer 8080
    exit 0
fi

# Method 3: Try Node.js
if command -v npx &> /dev/null; then
    echo "Using Node.js HTTP server on port 8080"
    echo "Open your browser to: http://localhost:8080"
    echo ""
    echo "Press Ctrl+C to stop the server"
    echo "================================================"
    npx http-server -p 8080
    exit 0
fi

# If no server is available
echo "Error: No web server available!"
echo ""
echo "Please install one of the following:"
echo "  - Python 3 (recommended)"
echo "  - Node.js"
echo ""
echo "Or simply open index.html directly in your web browser."
exit 1
