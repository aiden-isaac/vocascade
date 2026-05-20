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

def update_env(character_name, export_dir, wav_path, txt_path):
    env_file = REPO_ROOT / ".env"
    
    new_genie_vars = {
        "GENIE_TTS_URL": "http://127.0.0.1:8000",
        "GENIE_CHARACTER_NAME": character_name,
        "GENIE_ONNX_MODEL_DIR": str(export_dir.resolve()),
        "GENIE_REFERENCE_AUDIO": str(wav_path.resolve()),
        "GENIE_REFERENCE_TEXT": "",
        "GENIE_LANGUAGE": "en"
    }
    
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            new_genie_vars["GENIE_REFERENCE_TEXT"] = f.readline().strip()
    except Exception as e:
        print(f"Warning: Could not read reference text from {txt_path}: {e}")

    lines = []
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    output_lines = []
    found_keys = set()
    
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            output_lines.append(line)
            continue
            
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in new_genie_vars:
                output_lines.append(f"{key}={new_genie_vars[key]}\n")
                found_keys.add(key)
            elif key.startswith("GENIE_"):
                # Spec: preserving all non-GENIE_* lines (implicitly drops unused GENIE_ vars)
                pass
            else:
                output_lines.append(line)
        else:
            output_lines.append(line)
            
    for key, value in new_genie_vars.items():
        if key not in found_keys:
            output_lines.append(f"{key}={value}\n")
            
    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(output_lines)
        
    print("[4/4] Updated .env file with new character configuration:")
    for key, value in new_genie_vars.items():
        print(f"  + {key}={value}")

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
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing character profile if it exists")
    args = parser.parse_args()
    
    character_name = args.name
    profile_dir = PROFILES_DIR / character_name
    export_dir = profile_dir / "export"
    
    if profile_dir.exists() and not args.overwrite:
        print(f"Error: Character profile '{character_name}' already exists at '{profile_dir}'.")
        print("Use --overwrite to replace it.")
        sys.exit(1)
        
    print(f"Generating character profile: {character_name}")
    
    # 1. Validate files
    print("[1/4] Validating input files...")
    files = validate_input_files()
    
    # Check virtualenv python exists
    if not VENV_PYTHON.exists():
        print(f"Error: Genie TTS Python executable not found at '{VENV_PYTHON}'.")
        print("Please run 'scripts/setup_genie.sh' first.")
        sys.exit(1)
        
    # 2. Convert to ONNX
    print("[2/4] Converting models to ONNX format...")
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
        
    # 3. Copy reference files and cleanup staging
    print("[3/4] Archiving source models and reference files...")
    
    # We move all files from INPUT_DIR into profile_dir
    # (this includes .ckpt, .pth, .wav, .txt, and anything else in there)
    for item in INPUT_DIR.iterdir():
        if item.is_file() and item.name != "README.md":
            target_path = profile_dir / item.name
            if target_path.exists():
                target_path.unlink()
            shutil.move(str(item), str(target_path))
            
    # 4. Patch .env
    update_env(character_name, export_dir, profile_dir / files[".wav"].name, profile_dir / files[".txt"].name)
            
    print(f"\nSuccess! Character profile '{character_name}' generated at '{profile_dir}'.")

if __name__ == "__main__":
    main()
