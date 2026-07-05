@echo off
echo [1/3] Installing dependencies...
uv sync
echo [2/3] Initializing database...
uv run python -m app.setup db
echo [3/3] Converting NER model to ONNX (this takes a few minutes)...
uv sync --extra setup
uv run python -m app.setup ner
echo Setup complete. Run start.bat to launch the application.
pause
