#!/bin/bash
set -e

# Switch to the project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
WAKEWORD_DIR="$PROJECT_ROOT/static/wakeword"

echo "Downloading OpenWakeWord ONNX support models to $WAKEWORD_DIR..."

mkdir -p "$WAKEWORD_DIR"

cd "$WAKEWORD_DIR"

# Download melspectrogram.onnx
if [ ! -f "melspectrogram.onnx" ]; then
    echo "Downloading melspectrogram.onnx..."
    wget -q --show-progress https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx
else
    echo "melspectrogram.onnx already exists."
fi

# Download embedding_model.onnx
if [ ! -f "embedding_model.onnx" ]; then
    echo "Downloading embedding_model.onnx..."
    wget -q --show-progress https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx
else
    echo "embedding_model.onnx already exists."
fi

echo "Download complete!"
ls -la "$WAKEWORD_DIR"
