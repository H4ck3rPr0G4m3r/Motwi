"""Start with Windows. The registry's Run value is the only record, so Settings and the
installer's checkbox can never disagree."""

import sys
from typing import Protocol

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Motwi"


class RunValue(Protocol):
    def get(self) -> str | None: ...
    def set(self, command: str) -> None: ...
    def delete(self) -> None: ...


class WindowsRunValue:
    def __init__(self, key: str = RUN_KEY, name: str = VALUE_NAME) -> None:
        self._key = key
        self._name = name

    def get(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key) as k:
                return str(winreg.QueryValueEx(k, self._name)[0])
        except FileNotFoundError:
            return None

    def set(self, command: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._key) as k:
            winreg.SetValueEx(k, self._name, 0, winreg.REG_SZ, command)

    def delete(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, self._name)
        except FileNotFoundError:
            pass


def installed_exe() -> str | None:
    """The app's own .exe when installed. A dev checkout has nothing stable to register."""
    return sys.executable if getattr(sys, "frozen", False) else None


class AutoStart:
    def __init__(self, value: RunValue | None = None, exe: str | None = None) -> None:
        self._exe = exe or installed_exe()
        self._value = value or (WindowsRunValue() if sys.platform == "win32" else None)

    @property
    def available(self) -> bool:
        return self._exe is not None and self._value is not None

    @property
    def enabled(self) -> bool:
        return self._value is not None and self._value.get() is not None

    def set_enabled(self, on: bool) -> None:
        if not self.available:
            raise ValueError("Start with Windows only works in the installed app.")
        assert self._value is not None
        if on:
            self._value.set(f'"{self._exe}" --background')
        else:
            self._value.delete()
