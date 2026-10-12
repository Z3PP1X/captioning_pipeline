@echo off
echo ====================================================
echo Starting Captioning Pipeline inside WSL 2 (Ubuntu)...
echo Access the UI at: http://localhost:8000
echo ====================================================
wsl bash -lc "cd /mnt/c/Users/Patri/Documents/captioning_pipeline && bash scripts/run.sh"
pause
