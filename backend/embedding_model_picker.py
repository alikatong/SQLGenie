from __future__ import annotations

from pathlib import Path

from .config import BASE_DIR, validate_qwen_embedding_model_path


def _initial_directory(raw_path: object) -> Path:
    candidate = Path(str(raw_path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    if candidate.is_dir():
        return candidate
    if candidate.parent.is_dir():
        return candidate.parent
    return BASE_DIR


def pick_qwen_embedding_model_path(current_path: object = None) -> str | None:
    """Open a local directory chooser and validate the selected Qwen model."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError("当前 Python 环境不支持系统目录选择器。") from exc

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        selected = filedialog.askdirectory(
            title="选择 Qwen Embedding 模型目录",
            initialdir=str(_initial_directory(current_path)),
            mustexist=True,
        )
    except Exception as exc:
        raise RuntimeError("无法打开本地系统目录选择器。") from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass

    if not selected:
        return None
    return validate_qwen_embedding_model_path(selected)
