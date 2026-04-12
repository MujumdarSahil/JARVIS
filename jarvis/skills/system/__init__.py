"""System control skills: files, shell, clipboard, apps."""

from .apps import AppSkill
from .clipboard import ClipboardSkill
from .files import FileSkill
from .shell import ShellSkill

__all__ = ["AppSkill", "ClipboardSkill", "FileSkill", "ShellSkill"]
