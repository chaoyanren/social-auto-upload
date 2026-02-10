@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\alvinxds\.conda\envs\social\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "SOURCE_ROOT=%~4"
if "%SOURCE_ROOT%"=="" set "SOURCE_ROOT=D:\Development\sora-ai-video-downloader-python\videos"
set "DATE=%~1"
if "%DATE%"=="" set "DATE=latest"

set "LIMIT=%~2"
if "%LIMIT%"=="" set "LIMIT=0"

set "DAILY_TIMES=%~3"
if "%DAILY_TIMES%"=="" set "DAILY_TIMES=15"

set "COVER_STRATEGY=%~5"
if "%COVER_STRATEGY%"=="" set "COVER_STRATEGY=auto"

set "COVER_FRAME_SECONDS=%~6"
if "%COVER_FRAME_SECONDS%"=="" set "COVER_FRAME_SECONDS=5"

set "RECORD_FILE=videos/.sync_last.json"

echo [1/3] Sync from "%SOURCE_ROOT%" date=%DATE% limit=%LIMIT%
"%PYTHON_EXE%" sync_sora_daily_videos.py --source-root "%SOURCE_ROOT%" --date "%DATE%" --limit %LIMIT% --overwrite --record-file "%RECORD_FILE%" --cover-strategy %COVER_STRATEGY% --cover-frame-seconds %COVER_FRAME_SECONDS%
if errorlevel 1 (
  echo Sync failed.
  exit /b 1
)

echo [2/3] Upload only synced files to Douyin (daily_times=%DAILY_TIMES%)
"%PYTHON_EXE%" upload_synced_to_douyin.py --record-file "%RECORD_FILE%" --daily-times "%DAILY_TIMES%"
if %ERRORLEVEL% EQU 2 (
  echo No synced files to upload.
  "%PYTHON_EXE%" cleanup_synced_videos.py --record-file "%RECORD_FILE%"
  exit /b 0
)
if errorlevel 1 (
  echo Upload failed. Synced files are kept for retry.
  exit /b 1
)

echo [3/3] Cleanup synced files
"%PYTHON_EXE%" cleanup_synced_videos.py --record-file "%RECORD_FILE%"
if errorlevel 1 (
  echo Cleanup failed.
  exit /b 1
)

echo Done.
exit /b 0
