#!/usr/bin/env python3
"""
Genie TTS Character Generation Script
Automates the ONNX conversion of GPT-SoVITS models and organizes character profiles.
"""

import argparse
import sys
import os
from pathlib import Path
import subprocess
import shutil

# Paths
REPO_ROOT = Path(__file__).parent.parent.resolve()
INPUT_DIR = REPO_ROOT / "genie_input"
PROFILES_DIR = REPO_ROOT / "genie_profiles"
VENV_PYTHON = REPO_ROOT / "genie_tts_env" / "bin" / "python"

def validate_input_files():
    if not INPUT_DIR.exists():
        print(f"Error: Staging directory '{INPUT_DIR}' does not exist.")
        sys.exit(1)

    required_extensions = {".ckpt", ".pth", ".wav", ".txt"}
    found_files = {ext: [] for ext in required_extensions}
    
    for item in INPUT_DIR.iterdir():
        if item.is_file() and item.suffix in required_extensions:
            found_files[item.suffix].append(item)

    errors = []
    for ext, files in found_files.items():
        if len(files) == 0:
            errors.append(f"Missing required file type: {ext}")
        elif len(files) > 1:
            errors.append(f"Ambiguous file type (found multiple): {ext}")
            for f in files:
                errors.append(f"  - {f.name}")

    if errors:
        print("Error: Invalid files in 'genie_input/':")
        for err in errors:
            print(f"  {err}")
        sys.exit(1)

    return {ext: files[0] for ext, files in found_files.items()}

def main():
    parser = argparse.ArgumentParser(description="Generate a Genie TTS character profile.")
    parser.add_argument("--name", required=True, help="Name of the character profile to generate")
    args = parser.parse_args()
    
    character_name = args.name
    profile_dir = PROFILES_DIR / character_name
    export_dir = profile_dir / "export"
    
    print(f"Generating character profile: {character_name}")
    
    # 1. Validate files
    print("[1/3] Validating input files...")
    files = validate_input_files()
    
    # Check virtualenv python exists
    if not VENV_PYTHON.exists():
        print(f"Error: Genie TTS Python executable not found at '{VENV_PYTHON}'.")
        print("Please run 'scripts/setup_genie.sh' first.")
        sys.exit(1)
        
    # 2. Convert to ONNX
    print("[2/3] Converting models to ONNX format...")
    profile_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    
    ckpt_path = files[".ckpt"]
    pth_path = files[".pth"]
    
    convert_script = (
        "import sys; "
        "from genie_tts.Converter.Converter import convert; "
        f"convert(torch_ckpt_path='{ckpt_path}', torch_pth_path='{pth_path}', output_dir='{export_dir}')"
    )
    
    try:
        subprocess.run([str(VENV_PYTHON), "-c", convert_script], check=True, cwd=str(REPO_ROOT))
    except subprocess.CalledProcessError:
        print("Error: ONNX conversion failed.")
        sys.exit(1)
        
    # 3. Copy reference files
    print("[3/3] Copying reference audio and transcript...")
    wav_path = files[".wav"]
    txt_path = files[".txt"]
    
    shutil.copy2(wav_path, profile_dir / wav_path.name)
    shutil.copy2(txt_path, profile_dir / txt_path.name)
    
    print(f"\nSuccess! Character profile '{character_name}' generated at '{profile_dir}'.")

if __name__ == "__main__":
    main()
