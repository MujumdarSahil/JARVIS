"""
Local file operations with path resolution, encoding detection, and safety checks.
"""

from __future__ import annotations

import mimetypes
import os
import platform
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from send2trash import send2trash

from utils.logger import get_logger

logger = get_logger(__name__)

# Hard-coded protected Windows paths (cannot be disabled via config).
def _win_protected_roots() -> tuple[Path, ...]:
    if platform.system() != "Windows":
        return ()
    roots = (
        r"C:\Windows",
        r"C:\System32",
        r"C:\Program Files",
        r"C:\Program Files (x86)",
    )
    return tuple(Path(p).resolve() for p in roots)


_WIN_PROTECTED = _win_protected_roots()


def _ok_dict(success: bool, result: str = "", error: str | None = None) -> dict[str, Any]:
    return {"success": success, "result": result, "error": error}


def _resolve_path(path: str, *, op: str) -> tuple[Path | None, dict[str, Any] | None]:
    """Resolve to absolute path and log. Returns (path, error_dict) or (None, error)."""
    try:
        p = Path(path).expanduser()
        resolved = p.resolve()
        logger.info("FILE_OP %s resolved_path=%s", op, resolved)
        return resolved, None
    except (OSError, ValueError) as e:
        return None, _ok_dict(False, "", f"Invalid path: {e}")


def _is_protected_windows(path: Path) -> bool:
    if platform.system() != "Windows":
        return False
    try:
        rp = path.resolve()
    except (OSError, ValueError):
        return True
    for root in _WIN_PROTECTED:
        try:
            if root.exists():
                rp.relative_to(root)
                return True
        except ValueError:
            continue
    return False


def _check_protected(path: Path, allow_protected_path: bool, *, op: str) -> dict[str, Any] | None:
    if _is_protected_windows(path) and not allow_protected_path:
        return _ok_dict(
            False,
            "",
            "Refused: path is under a protected Windows system location "
            "(Windows, System32, or Program Files). "
            "Repeat the request with explicit confirmation and allow_protected_path=true in tool params.",
        )
    return None


def _detect_encoding_read(path: Path, max_chars: int) -> tuple[str, str]:
    """Read text trying common encodings; returns (text, encoding_name)."""
    raw = path.read_bytes()
    if len(raw) > max_chars * 4:
        raw = raw[: max_chars * 4]
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
        enc = "utf-8 (replacement)"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text, enc


class FileSkill:
    """Safe file and directory operations."""

    def list_dir(self, path: str = ".", *, allow_protected_path: bool = False) -> dict[str, Any]:
        try:
            resolved, err = _resolve_path(path, op="list_dir")
            if err:
                return err
            assert resolved is not None
            block = _check_protected(resolved, allow_protected_path, op="list_dir")
            if block:
                return block
            if not resolved.is_dir():
                return _ok_dict(False, "", "Not a directory")
            lines: list[str] = []
            for child in sorted(resolved.iterdir(), key=lambda p: p.name.lower()):
                try:
                    st = child.stat()
                    kind = "dir" if child.is_dir() else "file"
                    size = st.st_size if child.is_file() else 0
                    mtime = datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
                    lines.append(f"{kind:4}  {size:>12}  {mtime}  {child.name}")
                except OSError as e:
                    lines.append(f"err  {'—':>12}  —  {child.name} ({e})")
            return _ok_dict(True, "\n".join(lines) if lines else "(empty)", None)
        except Exception as e:
            logger.exception("list_dir failed: %s", e)
            return _ok_dict(False, "", str(e))

    def read_file(
        self,
        path: str,
        max_chars: int = 8000,
        *,
        allow_protected_path: bool = False,
    ) -> dict[str, Any]:
        try:
            cap = min(max(1, int(max_chars)), 8000)
            resolved, err = _resolve_path(path, op="read_file")
            if err:
                return err
            assert resolved is not None
            block = _check_protected(resolved, allow_protected_path, op="read_file")
            if block:
                return block
            if not resolved.is_file():
                return _ok_dict(False, "", "Not a file")
            text, enc = _detect_encoding_read(resolved, cap)
            header = f"[encoding={enc}, max_chars={cap}]\n"
            return _ok_dict(True, header + text, None)
        except Exception as e:
            logger.exception("read_file failed: %s", e)
            return _ok_dict(False, "", str(e))

    def write_file(
        self,
        path: str,
        content: str,
        overwrite: bool = False,
        *,
        allow_protected_path: bool = False,
    ) -> dict[str, Any]:
        try:
            resolved, err = _resolve_path(path, op="write_file")
            if err:
                return err
            assert resolved is not None
            block = _check_protected(resolved, allow_protected_path, op="write_file")
            if block:
                return block
            if resolved.exists() and not overwrite:
                return _ok_dict(False, "", "File exists; set overwrite=true to replace")
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8", newline="")
            return _ok_dict(True, f"Wrote {len(content)} characters to {resolved}", None)
        except Exception as e:
            logger.exception("write_file failed: %s", e)
            return _ok_dict(False, "", str(e))

    def delete_file(
        self,
        path: str,
        trash: bool = True,
        *,
        allow_protected_path: bool = False,
    ) -> dict[str, Any]:
        try:
            resolved, err = _resolve_path(path, op="delete_file")
            if err:
                return err
            assert resolved is not None
            block = _check_protected(resolved, allow_protected_path, op="delete_file")
            if block:
                return block
            if not resolved.exists():
                return _ok_dict(False, "", "Path does not exist")
            logger.info(
                "DELETE_FILE ts=%s path=%s trash=%s",
                datetime.now().isoformat(timespec="seconds"),
                resolved,
                trash,
            )
            if trash:
                send2trash(str(resolved))
                return _ok_dict(True, f"Moved to recycle bin: {resolved}", None)
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
            return _ok_dict(True, f"Permanently deleted: {resolved}", None)
        except Exception as e:
            logger.exception("delete_file failed: %s", e)
            return _ok_dict(False, "", str(e))

    def copy_file(
        self,
        src: str,
        dst: str,
        *,
        allow_protected_path: bool = False,
    ) -> dict[str, Any]:
        try:
            rs, e1 = _resolve_path(src, op="copy_file_src")
            if e1:
                return e1
            rd, e2 = _resolve_path(dst, op="copy_file_dst")
            if e2:
                return e2
            assert rs is not None and rd is not None
            for p, label in ((rs, "src"), (rd, "dst")):
                block = _check_protected(p, allow_protected_path, op=f"copy_file_{label}")
                if block:
                    return block
            if not rs.exists():
                return _ok_dict(False, "", "Source does not exist")
            if rs.is_dir():
                if rd.exists():
                    return _ok_dict(False, "", "Destination exists; remove it first for directory copy")
                shutil.copytree(rs, rd)
            else:
                rd.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(rs, rd)
            return _ok_dict(True, f"Copied {rs} -> {rd}", None)
        except Exception as e:
            logger.exception("copy_file failed: %s", e)
            return _ok_dict(False, "", str(e))

    def move_file(
        self,
        src: str,
        dst: str,
        *,
        allow_protected_path: bool = False,
    ) -> dict[str, Any]:
        try:
            rs, e1 = _resolve_path(src, op="move_file_src")
            if e1:
                return e1
            rd, e2 = _resolve_path(dst, op="move_file_dst")
            if e2:
                return e2
            assert rs is not None and rd is not None
            for p, label in ((rs, "src"), (rd, "dst")):
                block = _check_protected(p, allow_protected_path, op=f"move_file_{label}")
                if block:
                    return block
            if not rs.exists():
                return _ok_dict(False, "", "Source does not exist")
            rd.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(rs), str(rd))
            return _ok_dict(True, f"Moved {rs} -> {rd}", None)
        except Exception as e:
            logger.exception("move_file failed: %s", e)
            return _ok_dict(False, "", str(e))

    def create_folder(self, path: str, *, allow_protected_path: bool = False) -> dict[str, Any]:
        try:
            resolved, err = _resolve_path(path, op="create_folder")
            if err:
                return err
            assert resolved is not None
            block = _check_protected(resolved, allow_protected_path, op="create_folder")
            if block:
                return block
            resolved.mkdir(parents=True, exist_ok=True)
            return _ok_dict(True, f"Created (or exists): {resolved}", None)
        except Exception as e:
            logger.exception("create_folder failed: %s", e)
            return _ok_dict(False, "", str(e))

    def find_files(
        self,
        pattern: str,
        search_dir: str = ".",
        recursive: bool = True,
        *,
        allow_protected_path: bool = False,
    ) -> dict[str, Any]:
        try:
            base, err = _resolve_path(search_dir, op="find_files")
            if err:
                return err
            assert base is not None
            block = _check_protected(base, allow_protected_path, op="find_files")
            if block:
                return block
            if not base.is_dir():
                return _ok_dict(False, "", "search_dir is not a directory")
            matches: list[str] = []
            if recursive:
                for p in base.rglob(pattern):
                    if p.is_file():
                        matches.append(str(p.resolve()))
            else:
                for p in base.glob(pattern):
                    if p.is_file():
                        matches.append(str(p.resolve()))
            text = "\n".join(matches) if matches else "(no matches)"
            return _ok_dict(True, text, None)
        except Exception as e:
            logger.exception("find_files failed: %s", e)
            return _ok_dict(False, "", str(e))

    def get_file_info(self, path: str, *, allow_protected_path: bool = False) -> dict[str, Any]:
        try:
            resolved, err = _resolve_path(path, op="get_file_info")
            if err:
                return err
            assert resolved is not None
            block = _check_protected(resolved, allow_protected_path, op="get_file_info")
            if block:
                return block
            if not resolved.exists():
                return _ok_dict(False, "", "Path does not exist")
            st = resolved.stat()
            mode = stat.filemode(st.st_mode)
            mime, _enc = mimetypes.guess_type(str(resolved))
            created = getattr(st, "st_birthtime", st.st_ctime)
            lines = [
                f"path: {resolved}",
                f"type: {'dir' if resolved.is_dir() else 'file'}",
                f"size: {st.st_size}",
                f"created: {datetime.fromtimestamp(created).isoformat(timespec='seconds')}",
                f"modified: {datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds')}",
                f"permissions: {mode}",
                f"mime: {mime or 'unknown'}",
            ]
            return _ok_dict(True, "\n".join(lines), None)
        except Exception as e:
            logger.exception("get_file_info failed: %s", e)
            return _ok_dict(False, "", str(e))

    def open_file(self, path: str, *, allow_protected_path: bool = False) -> dict[str, Any]:
        try:
            resolved, err = _resolve_path(path, op="open_file")
            if err:
                return err
            assert resolved is not None
            block = _check_protected(resolved, allow_protected_path, op="open_file")
            if block:
                return block
            if not resolved.exists():
                return _ok_dict(False, "", "Path does not exist")
            if platform.system() == "Windows":
                os.startfile(str(resolved))  # noqa: S606
            elif platform.system() == "Darwin":
                import subprocess

                subprocess.Popen(["open", str(resolved)], check=False)
            else:
                import subprocess

                subprocess.Popen(["xdg-open", str(resolved)], check=False)
            return _ok_dict(True, f"Opened with default application: {resolved}", None)
        except Exception as e:
            logger.exception("open_file failed: %s", e)
            return _ok_dict(False, "", str(e))


if __name__ == "__main__":
    fs = FileSkill()
    print(fs.list_dir("."))
    print(fs.get_file_info(__file__))
