"""User-configurable settings, persisted as one JSON document."""

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal

from motwi.filters.spam import SpamConfig
from motwi.models import DEFAULT_LADDER, PRESETS, Kind, LadderStep, Thresholds


@dataclass(frozen=True)
class ProviderConfig:
    provider: Literal["gemini", "openrouter", "custom"]
    model: str
    base_url: str | None = None
    api_key_env: str = "MOTWI_AI_KEY"


def _pick(cls: type, d: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


def _provider_from(d: dict[str, Any] | None) -> ProviderConfig | None:
    return None if d is None else ProviderConfig(**_pick(ProviderConfig, d))


@dataclass(frozen=True)
class Settings:
    strictness: str = "standard"
    custom_thresholds: Thresholds | None = None
    ladder: tuple[LadderStep, ...] = DEFAULT_LADDER
    vip_leeway: float = 1.0
    suggest_only: bool = False
    hold_timeout_enabled: bool = True
    hold_timeout_s: int = 600
    warn_style: str = "twitch"
    delete_on_warn: bool = True
    batch_max: int = 8
    batch_wait_s: float = 1.5
    ai_timeout_s: float = 8.0
    context_lines: int = 12
    retention_days: int = 30
    primary: ProviderConfig | None = None
    backup: ProviderConfig | None = None
    spam: SpamConfig = field(default_factory=SpamConfig)
    setup_complete: bool = False
    paused: bool = False
    check_updates: bool = True

    def thresholds(self) -> Thresholds:
        if self.strictness == "custom":
            if self.custom_thresholds is None:
                raise ValueError("custom strictness requires custom_thresholds")
            return self.custom_thresholds
        return PRESETS[self.strictness]

    def validate(self) -> None:
        if self.strictness not in (*PRESETS, "custom"):
            raise ValueError(f"unknown strictness: {self.strictness!r}")
        if self.strictness == "custom" and self.custom_thresholds is None:
            raise ValueError("custom strictness requires custom_thresholds")
        checks = (
            ("vip_leeway", 0, 5),
            ("hold_timeout_s", 60, 1_209_600),
            ("batch_max", 1, 50),
            ("batch_wait_s", 0.2, 10),
            ("ai_timeout_s", 1, 60),
            ("context_lines", 0, 50),
            ("retention_days", 1, 365),
        )
        for name, lo, hi in checks:
            value = getattr(self, name)
            if not lo <= value <= hi:
                raise ValueError(f"{name} must be between {lo} and {hi}, got {value}")
        if self.warn_style not in ("twitch", "chat"):
            raise ValueError(f"unknown warn_style: {self.warn_style!r}")
        if not self.ladder:
            raise ValueError("ladder must not be empty")
        for step in self.ladder:
            if step.kind not in (Kind.WARN, Kind.TIMEOUT, Kind.BAN_REQUEST):
                raise ValueError(f"invalid ladder step kind: {step.kind}")
            if step.kind is Kind.TIMEOUT and not (step.duration_s and step.duration_s > 0):
                raise ValueError("timeout ladder steps need a positive duration_s")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ladder"] = [{"kind": s.kind.value, "duration_s": s.duration_s} for s in self.ladder]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Settings":
        kwargs = _pick(cls, d)
        if kwargs.get("custom_thresholds") is not None:
            kwargs["custom_thresholds"] = Thresholds(**kwargs["custom_thresholds"])
        if "ladder" in kwargs:
            kwargs["ladder"] = tuple(
                LadderStep(Kind(s["kind"]), s.get("duration_s")) for s in kwargs["ladder"]
            )
        for key in ("primary", "backup"):
            if key in kwargs:
                kwargs[key] = _provider_from(kwargs[key])
        if "spam" in kwargs:
            kwargs["spam"] = SpamConfig(**_pick(SpamConfig, kwargs["spam"]))
        return cls(**kwargs)
