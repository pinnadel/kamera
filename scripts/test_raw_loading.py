# test_raw_loading.py
# Day 1: Learning to load RAW files from my cameras
# Usage: python scripts/test_raw_loading.py <raf_file> <nef_file>

import sys
import rawpy
from rawpy._rawpy import RawPy, ThumbFormat
import numpy as np
from PIL import Image
import os

if len(sys.argv) < 3:
    print("Usage: python scripts/test_raw_loading.py <raf_file> <nef_file>")
    sys.exit(1)

raf_path = sys.argv[1]
nef_path = sys.argv[2]

print("=" * 70)
print("🎓 DAY 1: Learning RAW File Processing")
print("=" * 70)

# ==================== TEST 1: RAF (Fujifilm) ====================
print("\n📸 TEST 1: Loading RAF file from Fujifilm...")
filename = os.path.basename(raf_path)
print(f"   File: {filename}")

try:
    with rawpy.imread(raf_path) as raw:
        print(f"   ✅ RAF file loaded successfully!")
        print(f"   📷 Color description: {raw.color_desc.decode()}")
        print(f"   📐 Image size: {raw.sizes.width} x {raw.sizes.height} pixels")
        
        # Generate RGB preview
        rgb = raw.postprocess()
        print(f"   🎨 Preview generated!")
        print(f"   📊 Array shape: {rgb.shape}")
        print(f"      ↳ Meaning: ({rgb.shape[0]} height, {rgb.shape[1]} width, {rgb.shape[2]} color channels)")
        print(f"      ↳ Color channels: Red, Green, Blue")
        
        # Save preview
        img = Image.fromarray(rgb)
        # Resize for faster viewing (huge RAW files are slow)
        img.thumbnail((2000, 2000))
        preview_path = "fuji_preview.jpg"
        img.save(preview_path, quality=85)
        print(f"   💾 Saved as: {preview_path}")
        
except Exception as e:
    print(f"   ❌ Error: {e}")

# ==================== TEST 2: NEF (Nikon) ====================
print("\n📸 TEST 2: Loading NEF file from Nikon...")
filename = os.path.basename(nef_path)
print(f"   File: {filename}")

try:
    raw = RawPy()
    raw.open_file(nef_path)
    print(f"   ✅ NEF file opened successfully!")
    print(f"   📷 Color description: {raw.color_desc.decode()}")
    print(f"   📐 Image size: {raw.sizes.width} x {raw.sizes.height} pixels")

    # Extract the embedded JPEG thumbnail (full-res, camera-processed)
    thumb = raw.extract_thumb()
    raw.close()
    if thumb.format == ThumbFormat.JPEG:
        import io
        img = Image.open(io.BytesIO(thumb.data))
        print(f"   🎨 Embedded JPEG thumbnail extracted!")
        print(f"   📊 Thumbnail size: {img.width} x {img.height} px")
        img.thumbnail((2000, 2000))
        preview_path = "nikon_preview.jpg"
        img.save(preview_path, quality=85)
        print(f"   💾 Saved as: {preview_path}")
    else:
        print(f"   ⚠️  Thumbnail format not JPEG: {thumb.format}")

except Exception as e:
    print(f"   ❌ Error: {e}")

# ==================== What You Learned ====================
print("\n" + "=" * 70)
print("🎓 CONCEPTS LEARNED TODAY:")
print("=" * 70)
print("""
1. RAW Files Are Camera Sensor Data
   - Not processed like JPEG
   - Contain maximum information
   - Different formats: RAF (Fujifilm), NEF (Nikon)

2. Images Are Arrays of Numbers
   - Shape: (height, width, color_channels)
   - Each pixel has 3 values: Red, Green, Blue
   - This is how computers "see" images!

3. We Can Process Files Programmatically
   - Load RAW → Convert to RGB → Save as JPEG
   - This is the foundation for everything else!

4. Python Libraries Are Powerful
   - rawpy: Reads RAW files
   - PIL/Pillow: Saves images
   - numpy: Works with arrays (images are arrays!)
""")
print("=" * 70)
print("✨ Next session: Understanding sharpness detection!")
print("=" * 70)