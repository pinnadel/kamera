"""
Phase 2 Quality Analysis

Extends Phase 1 technical scoring with subject-aware signals:
  - face_analyzer: MediaPipe Face Mesh — blink detection, face sharpness, composition
"""

from .face_analyzer import analyze_faces

__all__ = ['analyze_faces']
