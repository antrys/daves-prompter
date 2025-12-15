@echo off
echo Starting Speech Prompter...
echo.

REM Open browser after a short delay (gives server time to start)
REM The ping command is used as a hacky sleep for 2 seconds because 'timeout' might be interrupted by input
ping 127.0.0.1 -n 3 > nul
start "" "http://localhost:8765"

REM Start the server
python server.py
pause
