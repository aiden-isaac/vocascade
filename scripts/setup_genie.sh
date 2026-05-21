#!/usr/bin/env bash
# Voice Satellite: Genie TTS Setup Automation
# This script sets up the Python virtual environment for Genie TTS
# and prepares the staging directory for character models.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="genie_tts_env"
INPUT_DIR="genie_input"

echo "========================================================"
echo " Genie TTS Automated Setup"
echo "========================================================"
echo

# 1. Create Python virtualenv
if [ ! -d "$VENV_DIR" ]; then
    echo "[1/4] Creating dedicated virtual environment at $VENV_DIR/..."
    python3.11 -m venv "$VENV_DIR" || python3 -m venv "$VENV_DIR"
else
    echo "[1/4] Virtual environment $VENV_DIR/ already exists. Skipping creation."
fi

# 2. Install genie-tts & torch (required for converter module)
echo "[2/4] Installing genie-tts and torch packages..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install torch genie-tts

# 3. Create staging directory
echo "[3/4] Creating staging directory at $INPUT_DIR/..."
mkdir -p "$INPUT_DIR"

# 4. Write README.md
echo "[4/4] Writing instructions to $INPUT_DIR/README.md..."
cat << 'EOF' > "$INPUT_DIR/README.md"
# Genie TTS Character Staging

Drop exactly four files into this directory to generate a new voice character profile:

1. `*.ckpt` - The GPT-SoVITS model checkpoint file
2. `*.pth` - The GPT model weights file
3. `*.wav` - The reference audio file (5-10s clip of the voice)
4. `*.txt` - The exact transcript of the reference audio file

Once all four files are present, run the generation script from the repository root:
```bash
python scripts/generate_character.py --name <character_name>
```
EOF

echo
echo "========================================================"
echo " Setup Complete ✓"
echo "========================================================"
echo "Next Steps:"
echo "1. Place your model files (.ckpt, .pth, .wav, .txt) into the 'genie_input/' directory."
echo "2. Run the character generation script to build the profile:"
echo
echo "    python scripts/generate_character.py --name <character_name>"
echo
echo "========================================================"
