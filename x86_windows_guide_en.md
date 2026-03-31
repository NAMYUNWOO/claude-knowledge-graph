# Windows Installation Guide

Guide for installing and running claude-knowledge-graph on Windows.

The original project targets macOS/Linux, so Windows requires code patches.
This document includes all issues encountered during actual installation on Windows 11 + RTX 3050.

---

## Requirements

| Item | Minimum | Recommended |
| --- | --- | --- |
| OS | Windows 10 x64 | Windows 11 |
| Python | 3.10+ | 3.13 |
| GPU | NVIDIA 4GB+ VRAM | 6GB+ VRAM |
| CUDA Driver | 12.x | 12.4+ |
| RAM | 16GB | 16GB+ |
| Claude Code | CLI installed | - |
| Obsidian | Optional | Required for graph view |

Check GPU:

```powershell
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader
# e.g.: NVIDIA GeForce RTX 3050, 8192 MiB, 5975 MiB
```

Check CUDA driver version:

```powershell
nvidia-smi
# Look for "CUDA Version: 12.7" in the top right
```

---

## Step 1: Clone the repo

```bash
git clone https://github.com/NAMYUNWOO/claude-knowledge-graph.git
cd claude-knowledge-graph
```

### Gotcha: Corporate proxy SSL error

If your corporate network uses self-signed certificates:

```
fatal: unable to access '...': SSL certificate problem: self-signed certificate in certificate chain
```

Workaround:

```bash
git -c http.sslVerify=false clone https://github.com/NAMYUNWOO/claude-knowledge-graph.git
```

---

## Step 2: Windows Compatibility Patches (Required)

There are 4 issues that must be fixed for Windows.

### Issue 1: `fcntl` module (Unix only)

`fcntl` is a Unix file locking module that does not exist on Windows.
It is used in both `qa_logger.py` and `qwen_processor.py`.

Windows alternative: `msvcrt.locking()` (Windows standard library)

**qa_logger.py** — replace `trigger_processor()` function:

```python
def _try_lock_nb(fd):
    """Try non-blocking exclusive lock. Returns True if acquired."""
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

def _unlock(fd):
    """Release file lock."""
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)

def trigger_processor() -> None:
    import subprocess

    lock_file = DATA_DIR / "processor.lock"
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_WRONLY)
        if not _try_lock_nb(fd):
            os.close(fd)
            log("Processor already running, skipping trigger")
            return

        _unlock(fd)
        os.close(fd)

        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        subprocess.Popen(
            [sys.executable, "-m", "claude_knowledge_graph.qwen_processor"],
            **popen_kwargs,
        )
        log("Triggered background processor")
    except Exception as e:
        log(f"Failed to trigger processor:{e}")
```

**qwen_processor.py** — replace lock logic in `main()`:

```python
def _acquire_lock(lock_fd):
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

def _release_lock(lock_fd):
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
```

Then replace `fcntl.flock` calls inside `main()` with `_acquire_lock` / `_release_lock`.

### Issue 2: `start_new_session=True` (Unix only)

`subprocess.Popen`'s `start_new_session=True` internally calls `setsid()`, which does not exist on Windows.

Windows alternative: `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`

(Already included in the Patch 1 code above)

### Issue 3: `python3` command not found

`hooks.py` registers `python3 -m claude_knowledge_graph.qa_logger` as a hook command, but `python3` often doesn't exist on Windows.

**hooks.py** fix:

```python
import sys

if sys.platform == "win32":
    HOOK_COMMAND = f'"{sys.executable}" -m claude_knowledge_graph.qa_logger'
else:
    HOOK_COMMAND = "python3 -m claude_knowledge_graph.qa_logger"
```

This registers the full path of the Python used for installation:

```
"C:\Users\<you>\AppData\Local\Programs\Python\Python313\python.exe" -m claude_knowledge_graph.qa_logger
```

### Issue 4: `cp949` encoding error (Korean Windows)

**This is the trickiest issue.**

The default encoding on Korean Windows is `cp949`. If `.read_text()` / `.write_text()` are called without specifying encoding, they use `cp949`. The moment Unicode characters like em-dash (`—`) or arrows (`→`) appear, it crashes:

```
UnicodeEncodeError: 'cp949' codec can't encode character '\u2014' in position 229
```

Or if Claude Code transcripts contain broken bytes:

```
UnicodeEncodeError: 'cp949' codec can't encode character '\udceb' in position 830
```

> **Note**: This issue also affects any non-UTF-8 default locale on Windows (e.g., `cp1252` for Western European). The fix is the same.

**Fix: Add `encoding="utf-8"` to every `read_text()` / `write_text()` / `open()` call in the project.**

Affected files:
- `qa_logger.py` — `read_text()` x2, `write_text()` x3, `open()` x1
- `qwen_processor.py` — `read_text()` x2, `write_text()` x1, `open()` x2
- `obsidian_writer.py` — `read_text()` x3, `write_text()` x7, `open()` x1
- `config.py` — `read_text()` x1
- `hooks.py` — `read_text()` x1, `write_text()` x1
- `cli.py` — `read_text()` x3, `write_text()` x2

Example:

```python
# Before
file.write_text(content)
file.read_text()
open(path, "a")

# After
file.write_text(content, encoding="utf-8")
file.read_text(encoding="utf-8")
open(path, "a", encoding="utf-8")
```

> **Missing even one will cause a runtime crash.** Hooks run on Claude Code's Stop event — if an error occurs, it shows as "Stop hook error" and Q&A capture fails.
> Verify with: `grep -rn "read_text()\|write_text(" src/ | grep -v encoding`

---

## Step 3: Install the package

```bash
cd claude-knowledge-graph
pip install -e .
```

### Gotcha: Multiple Python versions

If multiple Python versions are installed, the default `python` might be an older version like 3.6. This project requires Python 3.10+.

```bash
# Use the full path
C:\Users\<you>\AppData\Local\Programs\Python\Python313\python.exe -m pip install -e .
```

---

## Step 4: Install llama-server (Windows CUDA)

On macOS you can just `brew install llama.cpp`, but on Windows you need to download binaries manually.

### 4-1. Download binaries

Download two zip files from [llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases):

| File | Description | Size |
| --- | --- | --- |
| `llama-<ver>-bin-win-cuda-12.4-x64.zip` | llama-server.exe and other binaries | ~214MB |
| `cudart-llama-bin-win-cuda-12.4-x64.zip` | CUDA runtime DLLs | ~373MB |

> If your CUDA driver is 12.x, choose the `cuda-12.4` build. Verify with `nvidia-smi`.

### 4-2. Extract

**Both zips must be extracted to the same directory.** The CUDA DLLs and llama-server.exe must be in the same folder for GPU recognition.

```powershell
mkdir C:\Users\<you>\llama.cpp\bin
# Extract both zips into bin\
```

Final directory structure:

```
C:\Users\<you>\llama.cpp\bin\
├── llama-server.exe      ← main binary
├── llama.dll
├── ggml-cuda.dll         ← required for GPU recognition
├── cublas64_12.dll       ← CUDA runtime
├── cublasLt64_12.dll
├── cudart64_12.dll
└── ... (other DLLs)
```

Verify:

```powershell
C:\Users\<you>\llama.cpp\bin\llama-server.exe --version
```

### Gotcha: GPU not recognized

If `ggml-cuda.dll` is not in the same folder as `llama-server.exe`, it will run on CPU only.
Check the server log for `ggml_cuda_init: found 1 CUDA devices`.

---

## Step 5: Download GGUF model (~2.6GB)

### Gotcha: huggingface-cli doesn't work well on Windows

The `huggingface-cli download` command from the README often fails on Windows.
`Scripts/huggingface-cli.exe` may not be on PATH, or module path issues occur.

**Using the Python API directly is more reliable:**

```bash
pip install huggingface-hub
```

```python
python -c "
from huggingface_hub import hf_hub_download
print(hf_hub_download(
    'unsloth/Qwen3.5-4B-GGUF',
    filename='Qwen3.5-4B-Q4_K_M.gguf',
    local_dir='C:/Users/<you>/.local/share/claude-knowledge-graph/models/Qwen3.5-4B-GGUF'
))
"
```

If you get SSL errors behind a corporate proxy:

```python
import os
os.environ['CURL_CA_BUNDLE'] = ''  # or path to your corporate CA cert
```

| Model | Size | VRAM | Notes |
| --- | --- | --- | --- |
| Qwen3.5-4B Q4_K_M | ~2.6GB | ~3GB | Recommended for 4-8GB VRAM GPUs |
| Qwen3.5-9B Q4_K_XL | ~5.6GB | ~6.5GB | 8GB+ VRAM, higher quality |

---

## Step 6: Initialize

```bash
ckg init --vault-dir C:\Users\<you>\obsidian-vault
```

If llama-server is not auto-detected, you'll be prompted for the path:

```
llama-server: NOT FOUND (auto-detect failed)
  Enter llama-server path: C:\Users\<you>\llama.cpp\bin\llama-server.exe
```

Or write directly in config.json:

```json
{
  "vault_dir": "C:\\Users\\<you>\\obsidian-vault",
  "llama_server": "C:\\Users\\<you>\\llama.cpp\\bin\\llama-server.exe",
  "model_path": "C:\\Users\\<you>\\.local\\share\\claude-knowledge-graph\\models\\Qwen3.5-4B-GGUF\\Qwen3.5-4B-Q4_K_M.gguf"
}
```

Config file location: `~/.config/claude-knowledge-graph/config.json`

---

## Step 7: Verify

```bash
# Check status
ckg status
# Expected:
# Pending:   0
# Processed: 0
# Written:   0
# Total:     0
# Hooks: all registered

# Manual test run
ckg run
```

Verify hooks are properly registered:

```bash
python -c "
import json
with open('C:/Users/<you>/.claude/settings.json', encoding='utf-8') as f:
    s = json.load(f)
for event, groups in s.get('hooks', {}).items():
    for g in groups:
        for h in g.get('hooks', []):
            if 'knowledge-graph' in h.get('description', ''):
                print(f'{event}: {h[\"command\"]}')
"
```

Expected output:

```
UserPromptSubmit: "C:\Users\<you>\...\python.exe" -m claude_knowledge_graph.qa_logger
Stop: "C:\Users\<you>\...\python.exe" -m claude_knowledge_graph.qa_logger
```

---

## Step 8: Install Obsidian (Optional)

```powershell
winget install Obsidian.Obsidian
```

After installation, open the vault path (`C:\Users\<you>\obsidian-vault`) in Obsidian to view the auto-generated notes in graph view.

Generated `.md` files can also be opened with any text editor without Obsidian.

---

## Performance (Windows 11, RTX 3050 8GB)

| Item | Value |
| --- | --- |
| llama-server startup | ~3s |
| Tagging per Q&A | 4-5s |
| VRAM usage | ~3.4GB (Q4_K_M, full GPU offload) |
| Obsidian note generation | <1s |

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'fcntl'`

Patch 1 and 2 not applied. `fcntl` is a Unix-only module.

### `'cp949' codec can't encode character`

Patch 4 not applied or incomplete. A file is missing `encoding="utf-8"`.
Verify: `grep -rn "read_text()\|write_text(" src/ | grep -v encoding`

### `UnicodeEncodeError: '\udceb'` (surrogate character)

Occurs when Claude Code transcript files contain broken UTF-8 bytes.
Fixed by the `encoding="utf-8"` patch. If it persists, consider adding `errors="surrogateescape"`.

### `python3: command not found` (hook error)

Patch 3 not applied. `python3` doesn't exist on Windows.
`hooks.py` must use `sys.executable` to register the full path.
Re-run `ckg init` after patching (to re-register hooks).

### llama-server fails to start

1. Check CUDA DLL location — `cublas64_12.dll` etc. must be in the same folder as `llama-server.exe`
2. Check CUDA driver version — verify with `nvidia-smi`, use `cuda-12.4` build for 12.x drivers
3. Check logs: `~/.local/share/claude-knowledge-graph/logs/llama_server.log`

### GPU not recognized (CPU fallback)

Verify `ggml-cuda.dll` is in the same directory as `llama-server.exe`.
Server log should show `ggml_cuda_init: found 1 CUDA devices`.

### Model download fails behind corporate proxy

`huggingface-cli` doesn't work well behind proxies. Use the Python API (`hf_hub_download`) directly.
For SSL errors, set `HF_HUB_DISABLE_TELEMETRY=1` or `REQUESTS_CA_BUNDLE` environment variable.

---

## Patch Summary

| File | Issue | Windows Alternative |
| --- | --- | --- |
| `qa_logger.py` | `import fcntl` | `msvcrt.locking()` (conditional) |
| `qa_logger.py` | `start_new_session=True` | `CREATE_NEW_PROCESS_GROUP` |
| `qwen_processor.py` | `import fcntl` | `msvcrt.locking()` (conditional) |
| `hooks.py` | `python3` hardcoded | `sys.executable` full path |
| All 6 files | `read_text()` / `write_text()` no encoding | Add `encoding="utf-8"` |

> Total modifications: `fcntl` x2, `start_new_session` x1, `python3` x1,
> `encoding` ~25 locations (all read_text + write_text + open calls)
