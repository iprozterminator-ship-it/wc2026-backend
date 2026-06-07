@echo off
echo.
echo ========================================
echo   WC2026 — Deploy to Vercel
echo ========================================
echo.

cd /d "%~dp0"

:: Init git if not already done
if not exist ".git" (
    echo [1/4] Initialising git repo...
    git init
    git remote add origin https://github.com/iprozterminator-ship-it/wc2026-backend.git
    git branch -M main
) else (
    echo [1/4] Git repo ready.
)

:: Stage all changes
echo [2/4] Staging changes...
git add -A

:: Commit with timestamp
set TIMESTAMP=%date% %time%
echo [3/4] Committing...
git commit -m "update %TIMESTAMP%" 2>nul || echo Nothing new to commit.

:: Push
echo [4/4] Pushing to GitHub (Vercel will auto-deploy)...
git push -u origin main

echo.
echo ✅ Done! Check https://wc2026-nine-iota.vercel.app in ~30 seconds.
echo.
pause
