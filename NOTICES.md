# Third-party notices

KaMeRa is built on top of a number of open source projects and pre-trained machine-learning models. This document lists them, their licenses, and any usage caveats.

Nothing in KaMeRa transmits photos, metadata, or telemetry to any third party. All ML inference runs locally on the user's machine. Model weights are downloaded once from public sources (HuggingFace, the pyiqa weight cache, the `facenet-pytorch` release artifacts) into the user's local cache directory.

---

## Python dependencies

| Package | License | Project |
|---|---|---|
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Pydantic | MIT | https://github.com/pydantic/pydantic |
| uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| Pillow | MIT-CMU | https://github.com/python-pillow/Pillow |
| pillow-heif | BSD-3-Clause | https://github.com/bigcat88/pillow_heif |
| OpenCV (opencv-contrib-python) | Apache 2.0 | https://github.com/opencv/opencv-python |
| rawpy | MIT | https://github.com/letmaik/rawpy |
| NumPy | BSD-3-Clause | https://github.com/numpy/numpy |
| MediaPipe | Apache 2.0 | https://github.com/google-ai-edge/mediapipe |
| pyiqa (IQA-PyTorch) | Apache 2.0 | https://github.com/chaofengc/IQA-PyTorch |
| transformers (Hugging Face) | Apache 2.0 | https://github.com/huggingface/transformers |
| PyTorch | BSD-3-Clause | https://github.com/pytorch/pytorch |
| scikit-learn | BSD-3-Clause | https://github.com/scikit-learn/scikit-learn |
| facenet-pytorch | MIT (code) | https://github.com/timesler/facenet-pytorch |
| ExifRead | BSD-style (Gene Cash) | https://github.com/ianare/exif-py |
| PyExifTool | BSD-3-Clause (dual-licensed BSD/GPLv3+; BSD branch used) | https://github.com/sylikc/pyexiftool |
| watchdog | Apache 2.0 | https://github.com/gorakhargosh/watchdog |
| send2trash | BSD-3-Clause | https://github.com/arsenetar/send2trash |
| openai (Python SDK) | Apache 2.0 | https://github.com/openai/openai-python |
| python-dotenv | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| pystray | LGPLv3 | https://github.com/moses-palmer/pystray |
| rumps | BSD-3-Clause | https://github.com/jaredks/rumps |

**LGPLv3 note (pystray):** KaMeRa imports `pystray` dynamically as an unmodified PyPI package. No source modifications are made.

---

## Frontend dependencies

| Package | License | Project |
|---|---|---|
| React | MIT | https://github.com/facebook/react |
| Vite | MIT | https://github.com/vitejs/vite |
| Tailwind CSS | MIT | https://github.com/tailwindlabs/tailwindcss |
| `@tailwindcss/vite` | MIT | https://github.com/tailwindlabs/tailwindcss |
| react-hotkeys-hook | MIT | https://github.com/JohannesKlauss/react-hotkeys-hook |
| react-day-picker | MIT | https://github.com/gpbl/react-day-picker |
| lucide-react | ISC | https://github.com/lucide-icons/lucide |
| date-fns | MIT | https://github.com/date-fns/date-fns |
| ESLint and plugins | MIT | https://github.com/eslint/eslint |

---

## System dependencies (not bundled — user installs)

| Tool | License | Project |
|---|---|---|
| ExifTool (Perl binary) | Artistic License / GPL | https://exiftool.org |
| Ollama | MIT | https://github.com/ollama/ollama |

These are invoked as external processes. KaMeRa does not distribute their binaries.

---

## Pre-trained model weights

This is the part that matters for any future commercial use of KaMeRa. The libraries above are permissively licensed, but several of the model **weights** they load are released under research-only terms. KaMeRa is a non-commercial personal project, which keeps it within the bounds of every license below.

| Model | Used for | Weight license | Caveat |
|---|---|---|---|
| MediaPipe FaceLandmarker | Face landmarks + blendshapes (eyes, expression) | Apache 2.0 | Clean for any use. |
| MediaPipe BlazeFace (short-range) | Fallback face detector | Apache 2.0 | Clean for any use. |
| SigLIP-2 base / patch-16 | Image embeddings + zero-shot scene tags | Apache 2.0 (Google) | Clean for any use. |
| Ollama qwen2.5vl:7b | Optional LLM explanations of picks | Apache 2.0 (Alibaba) | Clean for any use. Loaded only if Ollama is installed. |
| TOPIQ-NR / TOPIQ-IAA (pyiqa) | No-reference image quality and aesthetic scores | **Research-only** — trained on KonIQ-10k, SPAQ, AVA datasets which restrict commercial redistribution of derived models | Personal / research / non-commercial use only. |
| CLIP ViT-L/14 (OpenAI) | Backbone for the LAION aesthetic predictor | **Research-only** (OpenAI original release) | Personal / research / non-commercial use only. |
| LAION aesthetic predictor v2 (linearMSE) | Aesthetic score head, sits on top of CLIP ViT-L/14 | MIT (linear head) — but inherits CLIP's research-only constraint via the backbone | Personal / research / non-commercial use only. |
| FaceNet InceptionResnet V1 (vggface2-pretrained) | 512-dim face identity embeddings for People mode | **Research-only** — VGGFace2 dataset is licensed for academic research only | Personal / research / non-commercial use only. |

**Practical implication:** as currently configured KaMeRa is a non-commercial project. If it were ever to become a paid product, TOPIQ, CLIP ViT-L/14, and the FaceNet/VGGFace2 weights would each need a clean-license replacement (e.g. CLIP-IQA+, OpenCLIP LAION-2B, InsightFace).

---

## Attribution

If you fork or build on KaMeRa, please retain this file and the relevant license texts. The full Apache 2.0 license text is available at https://www.apache.org/licenses/LICENSE-2.0.

If you notice an attribution missing or incorrect, please open an issue.
