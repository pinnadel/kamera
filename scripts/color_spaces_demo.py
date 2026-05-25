# color_spaces_demo.py
# Day 1 Extra: Understanding Color Spaces Visually

import cv2
import numpy as np
from PIL import Image
import matplotlib.pyplot as plt

print("=" * 70)
print("🎨 COLOR SPACES DEMO - Understanding How We Represent Images")
print("=" * 70)

# Load the preview we already created
print("\n📸 Loading your Nikon preview...")
nikon_preview_path = "nikon_preview.jpg"

try:
    # Load with OpenCV (it loads as BGR by default)
    img_bgr = cv2.imread(nikon_preview_path)

    if img_bgr is None:
        print("❌ Could not load preview. Make sure you ran test_raw_loading.py first!")
        exit(1)

    print(f"✅ Loaded: {img_bgr.shape}")

    # Convert BGR to RGB (OpenCV uses BGR, we want RGB for display)
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    # Create different color space versions
    print("\n🔄 Converting to different color spaces...")

    # 1. Grayscale (1 channel)
    img_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    print(f"   Grayscale shape: {img_gray.shape}")
    print(f"      ↳ Notice: Only 2 dimensions! (height, width)")
    print(f"      ↳ No color channels - just brightness values")

    # 2. HSV (3 channels: Hue, Saturation, Value)
    img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    print(f"   HSV shape: {img_hsv.shape}")
    print(f"      ↳ Still 3 channels, but different meaning:")
    print(f"      ↳ H = Hue (color type: red, blue, green)")
    print(f"      ↳ S = Saturation (color intensity)")
    print(f"      ↳ V = Value (brightness)")

    # Create a visualization
    print("\n🎨 Creating side-by-side comparison...")

    # Create figure with subplots
    fig, axes = plt.subplots(2, 3, figsize=(15, 10))
    fig.suptitle('🎨 Understanding Color Spaces - Your Photo in Different Representations',
                 fontsize=16, fontweight='bold')

    # Row 1: Full images
    # RGB
    axes[0, 0].imshow(img_rgb)
    axes[0, 0].set_title('RGB (Red, Green, Blue)\nHow cameras capture color\n3 channels',
                         fontsize=12, fontweight='bold')
    axes[0, 0].axis('off')

    # Grayscale
    axes[0, 1].imshow(img_gray, cmap='gray')
    axes[0, 1].set_title('Grayscale (Brightness only)\nUsed for sharpness detection\n1 channel',
                         fontsize=12, fontweight='bold')
    axes[0, 1].axis('off')

    # HSV (convert to RGB for display)
    img_hsv_rgb = cv2.cvtColor(img_hsv, cv2.COLOR_HSV2RGB)
    axes[0, 2].imshow(img_hsv_rgb)
    axes[0, 2].set_title('HSV (Hue, Saturation, Value)\nBetter for color selection\n3 channels',
                         fontsize=12, fontweight='bold')
    axes[0, 2].axis('off')

    # Row 2: Individual channels
    # RGB channels separated
    rgb_channels = cv2.split(img_rgb)
    rgb_combined = np.hstack([rgb_channels[0], rgb_channels[1], rgb_channels[2]])
    axes[1, 0].imshow(rgb_combined, cmap='gray')
    axes[1, 0].set_title('RGB Channels Separated\nRed | Green | Blue',
                         fontsize=10, style='italic')
    axes[1, 0].axis('off')

    # Grayscale (same as brightness channel — shows what sharpness algo sees)
    axes[1, 1].imshow(img_gray, cmap='gray')
    axes[1, 1].set_title('Grayscale Channel\nWhat sharpness detection sees',
                         fontsize=10, style='italic')
    axes[1, 1].axis('off')

    # HSV channels separated
    hsv_channels = cv2.split(img_hsv)
    hsv_combined = np.hstack([hsv_channels[0], hsv_channels[1], hsv_channels[2]])
    axes[1, 2].imshow(hsv_combined, cmap='gray')
    axes[1, 2].set_title('HSV Channels Separated\nHue | Saturation | Value',
                         fontsize=10, style='italic')
    axes[1, 2].axis('off')

    plt.tight_layout()
    output_path = "color_spaces_comparison.png"
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"   💾 Saved as: {output_path}")
    plt.show()
    print("\n✅ Done! Check color_spaces_comparison.png")

except Exception as e:
    print(f"❌ Error: {e}")
    raise
