import os
import glob
import sys
import subprocess
from pathlib import Path

def main():
    current_dir = Path.cwd()
    print(f"Scanning directory: {current_dir}")
    
    ckpts = list(current_dir.glob("*.ckpt"))
    pths = list(current_dir.glob("*.pth"))
    wavs = list(current_dir.glob("*.wav"))
    txts = list(current_dir.glob("*.txt"))
    
    if not ckpts or not pths or not wavs or not txts:
        print("Error: Missing required files for Genie TTS export.")
        print("Please ensure the following files are in the current directory:")
        print("  - 1x .ckpt file (SoVITS weights)")
        print("  - 1x .pth file (GPT weights)")
        print("  - 1x .wav file (Reference audio)")
        print("  - 1x .txt file (Reference text)")
        print("\nFound:")
        print(f"  .ckpt: {[p.name for p in ckpts]}")
        print(f"  .pth:  {[p.name for p in pths]}")
        print(f"  .wav:  {[p.name for p in wavs]}")
        print(f"  .txt:  {[p.name for p in txts]}")
        sys.exit(1)
        
    ckpt_file = ckpts[0]
    pth_file = pths[0]
    wav_file = wavs[0]
    txt_file = txts[0]
    
    print("\nDetected files:")
    print(f"  SoVITS model: {ckpt_file.name}")
    print(f"  GPT model:    {pth_file.name}")
    print(f"  Ref audio:    {wav_file.name}")
    print(f"  Ref text:     {txt_file.name}")
    
    export_dir = current_dir / "export"
    export_dir.mkdir(exist_ok=True)
    print(f"\nCreated export directory: {export_dir}")
    
    with open(txt_file, "r", encoding="utf-8") as f:
        ref_text = f.read().strip()
        
    print(f"\nReference text loaded: '{ref_text[:50]}...'")
    
    print("\nInitiating export...")
    
    try:
        from genie_tts.exporter import export_model
        print("Running genie_tts.exporter.export_model...")
        export_model(
            gpt_path=str(pth_file),
            sovits_path=str(ckpt_file),
            ref_audio=str(wav_file),
            ref_text=ref_text,
            output_dir=str(export_dir)
        )
        print("\nExport completed successfully!")
    except ImportError:
        print("genie_tts package not found in current environment.")
        print("Trying CLI fallback...")
        
        cmd = [
            sys.executable, "-m", "genie_tts.export",
            "--gpt", str(pth_file),
            "--sovits", str(ckpt_file),
            "--ref_audio", str(wav_file),
            "--ref_text", ref_text,
            "--output", str(export_dir)
        ]
        
        try:
            subprocess.run(cmd, check=True)
            print("\nExport completed successfully via CLI!")
        except subprocess.CalledProcessError as e:
            print(f"\nExport failed with exit code {e.returncode}")
            sys.exit(1)
            
if __name__ == "__main__":
    main()
