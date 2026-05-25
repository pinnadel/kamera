# Coding Patterns

Lessons learned building this project. Language-level and framework-level — not workflow.

---

## The Diagram-First Pattern

Before implementing any data structure, schema, or UI layout, ask for a visual representation first:

```
"Show me the proposed [schema / component tree / data flow] as an ASCII diagram.
Once I confirm the design, we'll implement it."
```

Changes to a diagram take seconds; changes to code take minutes. This is the UX design process applied to software architecture. Caught the `decisions.image_id UNIQUE` constraint before implementation this way.

---

## Python Backend Patterns

### Inspect Before You Consume

Before writing code that reads a function's output, print what it actually returns:

```python
python3 -c "from module import fn; import pprint; pprint.pprint(fn('test_file.RAF'))"
```

Actual key names (`laplacian_variance`, `exposure_warning`) often differ from assumed names. This prevents KeyError debugging sessions.

### Backend as Bridge to the OS

The local Python backend can do things browsers cannot — filesystem access, macOS scripts, folder watching.

**Native macOS folder picker via AppleScript:**
```python
script = 'tell application "System Events" to activate\nset p to choose folder with prompt "Select folder:"'
result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
return {"path": result.stdout.strip().replace("alias ", "").rstrip(":")} if result.returncode == 0 else {"path": None}
```
Frontend: on input click → POST /pick-folder → populate field with returned path.

### Progress Tracking Without a Queue System

For single-user local apps, a module-level dict is enough — no Redis, no queues, no locks needed.

```python
_progress = {"running": False, "total": 0, "done": 0, "current_file": None, "started_at": None}

# In long-running endpoint:
_progress.update({"running": True, "total": len(files), "done": 0, "started_at": time.monotonic()})
for f in files:
    _progress["current_file"] = f.name
    analyze(f)
    _progress["done"] += 1

# Separate GET /analyze-progress endpoint returns _progress + computed pct + eta
```

### Full-Stack Wiring Rule

Ask Claude to explain the request/response cycle before writing any endpoint. A 3-sentence explanation prevents hours of debugging wiring issues.

Pattern that worked:
1. Explain concept first: "How does FastAPI request/response work?"
2. Schema/diagram before implementation
3. Implement one layer at a time: DB → API → Frontend
4. Manual curl test after each endpoint before touching the frontend
5. Only wire frontend to API after API is confirmed working

---

## React Patterns

### Poll Every 400ms Instead of Streaming

For local apps with small batches, polling is simpler to implement and easier to understand.

```jsx
useEffect(() => {
  if (!analyzing) { setProgress(null); return }
  const id = setInterval(() => {
    fetch(`${API}/analyze-progress`).then(r => r.json()).then(setProgress)
  }, 400)
  return () => clearInterval(id)
}, [analyzing])
```

### Read CSS State at Runtime

When you need to know how many columns a grid has, read the actual rendered layout — don't hardcode breakpoints:

```js
const cols = () => getComputedStyle(gridRef.current).gridTemplateColumns.split(' ').length
```

This always matches what the user sees, regardless of Tailwind changes.

### useCallback Prevents Stale Closures in Hotkeys

```jsx
// WITHOUT useCallback: cols() captures old value at mount time
// WITH useCallback: always reads current DOM state
const cols = useCallback(() => ..., [])
```

### Sticky Header (Tailwind)

```jsx
<div className="sticky top-0 z-40 bg-[#0f0f0f] pb-3 mb-1">
  {/* header + controls + status bar */}
</div>
```

`bg-[#0f0f0f]` must match the body background — without it, content scrolls visible through the header.

### Portrait Image Rotation

Cameras write portrait photos as landscape + EXIF orientation tag. Fix server-side before sending to browser:

```python
from PIL import ImageOps
img = ImageOps.exif_transpose(img)  # bakes rotation into pixels, strips EXIF tag
```

Then in frontend: use `object-contain` (not `object-cover`) so the full image fits without cropping.

---

**Last Updated:** 2026-04-22
