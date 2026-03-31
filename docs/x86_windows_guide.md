# install-windows

# Windows Installation Guide

claude-knowledge-graph를 Windows에서 설치하고 운영하기 위한 가이드.

원본 프로젝트는 macOS/Linux를 타겟으로 설계되었기 때문에, Windows에서는 코드 패치가 필요합니다.
이 문서는 실제 Windows 11 + RTX 3050 환경에서 설치하면서 겪은 모든 시행착오를 포함합니다.

---

## 환경 요구사항

| 항목 | 최소 | 권장 |
| --- | --- | --- |
| OS | Windows 10 x64 | Windows 11 |
| Python | 3.10+ | 3.13 |
| GPU | NVIDIA 4GB+ VRAM | 6GB+ VRAM |
| CUDA Driver | 12.x | 12.4+ |
| RAM | 16GB | 16GB+ |
| Claude Code | CLI 설치됨 | - |
| Obsidian | 선택사항 | 그래프 뷰 사용 시 필요 |

GPU 확인:

```powershell
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader
# 예: NVIDIA GeForce RTX 3050, 8192 MiB, 5975 MiB
```

CUDA 드라이버 버전 확인:

```powershell
nvidia-smi
# 우측 상단에 "CUDA Version: 12.7" 같은 표시
```

---

## Step 1: 레포 클론

```bash
git clone https://github.com/NAMYUNWOO/claude-knowledge-graph.git
cd claude-knowledge-graph
```

### 삽질 포인트: 사내 프록시 SSL 에러

회사 네트워크에서 self-signed 인증서를 쓰는 경우:

```
fatal: unable to access '...': SSL certificate problem: self-signed certificate in certificate chain
```

해결:

```bash
git -c http.sslVerify=false clone https://github.com/NAMYUNWOO/claude-knowledge-graph.git
```

---

## Step 2: Windows 호환 패치 (필수)

Windows에서 반드시 수정해야 하는 4가지 문제가 있습니다.

### 문제 1: `fcntl` 모듈 (Unix 전용)

`fcntl`은 Unix 파일 잠금 모듈로, Windows에 존재하지 않습니다.
`qa_logger.py`와 `qwen_processor.py` 두 파일에서 사용합니다.

Windows 대체: `msvcrt.locking()` (Windows 표준 라이브러리)

**qa_logger.py** — `trigger_processor()` 함수 교체:

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

**qwen_processor.py** — `main()` 함수의 잠금 로직 교체:

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

그리고 `main()` 안의 `fcntl.flock` 호출을 `_acquire_lock` / `_release_lock`으로 교체.

### 문제 2: `start_new_session=True` (Unix 전용)

`subprocess.Popen`의 `start_new_session=True`는 내부적으로 `setsid()`를 호출하는데,
Windows에는 없습니다.

Windows 대체: `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`

(위 Patch 1의 코드에 이미 포함되어 있음)

### 문제 3: `python3` 명령 없음

hooks.py에서 `python3 -m claude_knowledge_graph.qa_logger`를 hook 명령으로 등록하는데,
Windows에서는 `python3`이라는 명령이 없는 경우가 많습니다.

**hooks.py** 수정:

```python
import sys

if sys.platform == "win32":
    HOOK_COMMAND = f'"{sys.executable}" -m claude_knowledge_graph.qa_logger'
else:
    HOOK_COMMAND = "python3 -m claude_knowledge_graph.qa_logger"
```

이렇게 하면 설치에 사용한 Python의 전체 경로가 hook에 등록됩니다:

```
"C:\Users\<you>\AppData\Local\Programs\Python\Python313\python.exe" -m claude_knowledge_graph.qa_logger
```

### 문제 4: `cp949` 인코딩 에러 (한국어 Windows)

**이것이 가장 까다로운 문제입니다.**

한국어 Windows의 기본 인코딩은 `cp949`인데, `.read_text()` / `.write_text()`에
encoding을 지정하지 않으면 cp949를 사용합니다. em-dash(`—`), 화살표(`→`) 같은
유니코드 문자가 포함된 순간 에러가 터집니다:

```
UnicodeEncodeError: 'cp949' codec can't encode character '\u2014' in position 229
```

또는 Claude Code의 transcript에 깨진 바이트가 있으면:

```
UnicodeEncodeError: 'cp949' codec can't encode character '\udceb' in position 830
```

**해결: 프로젝트 전체의 모든 `read_text()` / `write_text()` / `open()` 호출에
`encoding="utf-8"`을 추가합니다.**

대상 파일 목록:
- `qa_logger.py` — `read_text()` 2곳, `write_text()` 3곳, `open()` 1곳
- `qwen_processor.py` — `read_text()` 2곳, `write_text()` 1곳, `open()` 2곳
- `obsidian_writer.py` — `read_text()` 3곳, `write_text()` 7곳, `open()` 1곳
- `config.py` — `read_text()` 1곳
- `hooks.py` — `read_text()` 1곳, `write_text()` 1곳
- `cli.py` — `read_text()` 3곳, `write_text()` 2곳

예시:

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

> **하나라도 빠지면 런타임에 터집니다.** 특히 hook은 Claude Code의 Stop 이벤트에서
실행되기 때문에, 에러가 발생하면 “Stop hook error”로 표시되고 Q&A 캡처가 실패합니다.
`grep -rn "read_text()\|write_text(" *.py | grep -v encoding` 으로 누락 검증 가능.
> 

---

## Step 3: 패키지 설치

```bash
cd claude-knowledge-graph
pip install -e .
```

### 삽질 포인트: Python 여러 버전

Windows에 Python이 여러 버전 설치되어 있으면 기본 `python`이 3.6 같은 구버전일 수 있습니다.
이 프로젝트는 Python 3.10+가 필요합니다.

```bash
# 전체 경로로 설치
C:\Users\<you>\AppData\Local\Programs\Python\Python313\python.exe -m pip install -e .
```

---

## Step 4: llama-server 설치 (Windows CUDA)

macOS는 `brew install llama.cpp`로 끝나지만, Windows는 수동으로 바이너리를 받아야 합니다.

### 4-1. 바이너리 다운로드

[llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)에서 두 개의 zip을 받습니다:

| 파일 | 설명 | 크기 |
| --- | --- | --- |
| `llama-<ver>-bin-win-cuda-12.4-x64.zip` | llama-server.exe 등 바이너리 | ~214MB |
| `cudart-llama-bin-win-cuda-12.4-x64.zip` | CUDA 런타임 DLL | ~373MB |

> CUDA 드라이버가 12.x면 `cuda-12.4` 빌드 선택. `nvidia-smi`로 확인.
> 

### 4-2. 압축 해제

**두 zip을 같은 디렉토리에 풀어야 합니다.** CUDA DLL과 llama-server.exe가 같은 폴더에 있어야 GPU를 인식합니다.

```powershell
mkdir C:\Users\<you>\llama.cpp\bin
# 두 zip을 모두 bin\ 에 풀기
```

최종 디렉토리 구조:

```
C:\Users\<you>\llama.cpp\bin\
├── llama-server.exe      ← 메인 바이너리
├── llama.dll
├── ggml-cuda.dll         ← GPU 인식에 필수
├── cublas64_12.dll       ← CUDA 런타임
├── cublasLt64_12.dll
├── cudart64_12.dll
└── ... (기타 DLL)
```

검증:

```powershell
C:\Users\<you>\llama.cpp\bin\llama-server.exe --version
```

### 삽질 포인트: GPU 미인식

`ggml-cuda.dll`이 `llama-server.exe`와 같은 폴더에 없으면 CPU로만 동작합니다.
서버 로그에 `ggml_cuda_init: found 1 CUDA devices`가 보이는지 확인.

---

## Step 5: GGUF 모델 다운로드 (~2.6GB)

### 삽질 포인트: huggingface-cli 안 됨

README에 나온 `huggingface-cli download` 명령은 Windows에서 잘 안 됩니다.
`Scripts/huggingface-cli.exe`가 PATH에 없거나, 모듈 경로 문제가 발생합니다.

**Python API로 직접 다운로드하는 게 확실합니다:**

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

사내 프록시 뒤에서 SSL 에러가 나면:

```python
import os
os.environ['CURL_CA_BUNDLE'] = ''  # 또는 회사 CA 인증서 경로
```

| 모델 | 크기 | VRAM | 비고 |
| --- | --- | --- | --- |
| Qwen3.5-4B Q4_K_M | ~2.6GB | ~3GB | 4-8GB VRAM GPU 권장 |
| Qwen3.5-9B Q4_K_XL | ~5.6GB | ~6.5GB | 8GB+ VRAM, 더 좋은 품질 |

---

## Step 6: 초기화

```bash
ckg init --vault-dir C:\Users\<you>\obsidian-vault
```

llama-server가 자동 감지되지 않으면 경로를 물어봅니다:

```
llama-server: NOT FOUND (auto-detect failed)
  Enter llama-server path: C:\Users\<you>\llama.cpp\bin\llama-server.exe
```

또는 config.json에 직접 작성:

```json
{
  "vault_dir": "C:\\Users\\<you>\\obsidian-vault",
  "llama_server": "C:\\Users\\<you>\\llama.cpp\\bin\\llama-server.exe",
  "model_path": "C:\\Users\\<you>\\.local\\share\\claude-knowledge-graph\\models\\Qwen3.5-4B-GGUF\\Qwen3.5-4B-Q4_K_M.gguf"
}
```

Config 파일 위치: `~/.config/claude-knowledge-graph/config.json`

---

## Step 7: 검증

```bash
# 상태 확인
ckg status
# Expected:
# Pending:   0
# Processed: 0
# Written:   0
# Total:     0
# Hooks: all registered

# 수동 실행 테스트
ckg run
```

Hook이 제대로 등록되었는지 확인:

```bash
# settings.json에서 hook 명령 확인
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

정상이면 이런 출력:

```
UserPromptSubmit: "C:\Users\<you>\...\python.exe" -m claude_knowledge_graph.qa_logger
Stop: "C:\Users\<you>\...\python.exe" -m claude_knowledge_graph.qa_logger
```

---

## Step 8: Obsidian 설치 (선택)

```powershell
winget install Obsidian.Obsidian
```

설치 후 vault 경로(`C:\Users\<you>\obsidian-vault`)를 열면 knowledge-graph 폴더에
자동 생성된 노트들을 그래프 뷰로 볼 수 있습니다.

Obsidian 없이도 생성된 `.md` 파일은 아무 에디터로 열 수 있습니다.

---

## 실제 성능 (Windows 11, RTX 3050 8GB)

| 항목 | 값 |
| --- | --- |
| llama-server 기동 | ~3초 |
| Q&A 1건 태깅 | 4-5초 |
| VRAM 사용량 | ~3.4GB (Q4_K_M, 전 레이어 GPU 오프로드) |
| Obsidian 노트 생성 | <1초 |

---

## 트러블슈팅

### `ModuleNotFoundError: No module named 'fcntl'`

Patch 1, 2 미적용. `fcntl`은 Unix 전용 모듈.

### `'cp949' codec can't encode character`

Patch 4 미적용 또는 누락. `encoding="utf-8"` 빠진 파일이 있음.
검증: `grep -rn "read_text()\|write_text(" src/ | grep -v encoding`

### `UnicodeEncodeError: '\udceb'` (서로게이트 문자)

Claude Code의 transcript 파일에 깨진 UTF-8 바이트가 있을 때 발생.
`encoding="utf-8"` 패치로 해결. 패치 후에도 나오면 `errors="surrogateescape"` 추가 고려.

### `python3: command not found` (hook 에러)

Patch 3 미적용. Windows에서는 `python3` 명령이 없음.
hooks.py에서 `sys.executable`로 전체 경로를 등록해야 함.
패치 후 `ckg init` 재실행 필요 (hook 재등록).

### llama-server 기동 실패

1. CUDA DLL 위치 확인 — `cublas64_12.dll` 등이 `llama-server.exe`와 같은 폴더에 있어야 함
2. CUDA 드라이버 버전 확인 — `nvidia-smi`로 확인, 12.x 드라이버에는 cuda-12.4 빌드 사용
3. 로그 확인: `~/.local/share/claude-knowledge-graph/logs/llama_server.log`

### GPU 미인식 (CPU fallback)

`ggml-cuda.dll`이 `llama-server.exe`와 같은 디렉토리에 있는지 확인.
서버 로그에 `ggml_cuda_init: found 1 CUDA devices`가 있어야 정상.

### 사내 프록시에서 모델 다운로드 실패

`huggingface-cli`는 프록시 뒤에서 잘 안 됨. Python API (`hf_hub_download`)를 직접 사용.
SSL 에러 시 `HF_HUB_DISABLE_TELEMETRY=1` 또는 `REQUESTS_CA_BUNDLE` 환경변수 설정.

---

## 패치 요약

| 파일 | 문제 | Windows 대체 |
| --- | --- | --- |
| `qa_logger.py` | `import fcntl` | `msvcrt.locking()` (조건 분기) |
| `qa_logger.py` | `start_new_session=True` | `CREATE_NEW_PROCESS_GROUP` |
| `qwen_processor.py` | `import fcntl` | `msvcrt.locking()` (조건 분기) |
| `hooks.py` | `python3` 하드코딩 | `sys.executable` 전체 경로 |
| 전체 6개 파일 | `read_text()` / `write_text()` 인코딩 미지정 | `encoding="utf-8"` 추가 |

> 총 수정 지점: `fcntl` 2곳, `start_new_session` 1곳, `python3` 1곳,
`encoding` ~25곳 (read_text + write_text + open 전부)
>