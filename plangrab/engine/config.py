"""Load ``config.toml`` into a small typed object.

Uses stdlib ``tomllib`` (Python 3.11+, which the bundled portable runtime is);
falls back to the pure-Python ``tomli`` package for dev on older interpreters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - dev on <3.11
    import tomli as tomllib  # type: ignore

DEFAULT_TEMPLATE = "{index:03d} of {total:03d} - {title} - {plan_number} - {date}"
DEFAULT_UA = "PlanGrab/0.1 (+planning document downloader; contact site admin if issues)"
# Origin of the hosted GitHub Pages UI, allowed to call the local helper (CORS).
DEFAULT_ALLOWED_ORIGIN = "https://gtlaix.github.io"
# Ports the local helper tries in order; the hosted page probes the same list.
DEFAULT_PORTS = [8756, 8757, 8758, 8759, 8760]


@dataclass
class Config:
    naming_template: str = DEFAULT_TEMPLATE
    date_format: str = "%d %b %Y"
    user_agent: str = DEFAULT_UA
    request_delay: float = 0.7          # polite gap between requests, seconds
    timeout: float = 60.0               # per-request timeout, seconds
    max_retries: int = 3                # transient-error retries per request
    tls_verify: bool = True             # false = skip TLS verification (last resort
                                        # for corporate proxies; see README)
    registry_update: bool = True        # refresh council registry from the repo on startup
    registry_update_url: str = (
        "https://raw.githubusercontent.com/gtlaix/PlanGrab/master/data")
    lpa_registry: dict[str, str] = field(default_factory=dict)  # host -> council name
    system_user_agents: dict[str, str] = field(default_factory=dict)  # system id -> UA override
    allowed_origin: str = DEFAULT_ALLOWED_ORIGIN  # hosted UI origin allowed to call the helper
    ports: list[int] = field(default_factory=lambda: list(DEFAULT_PORTS))  # helper port candidates
    open_browser: bool = False          # open a local UI tab on start (off: run as a quiet helper)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """Load from ``path`` (default: config.toml next to the package root)."""
        if path is None:
            path = Path(__file__).resolve().parent.parent.parent / "config.toml"
        path = Path(path)
        if not path.exists():
            return cls()

        with path.open("rb") as f:
            data = tomllib.load(f)

        naming = data.get("naming", {})
        net = data.get("network", {})
        server = data.get("server", {})
        ports_raw = server.get("ports", DEFAULT_PORTS)
        ports = [int(p) for p in ports_raw] if ports_raw else list(DEFAULT_PORTS)
        return cls(
            naming_template=naming.get("template", DEFAULT_TEMPLATE),
            date_format=naming.get("date_format", "%d %b %Y"),
            user_agent=net.get("user_agent", DEFAULT_UA),
            request_delay=float(net.get("request_delay", 0.7)),
            timeout=float(net.get("timeout", 60.0)),
            max_retries=int(net.get("max_retries", 3)),
            tls_verify=bool(net.get("tls_verify", True)),
            registry_update=bool(data.get("registry_update", {}).get("enabled", True)),
            registry_update_url=str(data.get("registry_update", {}).get(
                "url", cls.registry_update_url)),
            lpa_registry={k.lower(): v for k, v in data.get("lpa_registry", {}).items()},
            system_user_agents={k.lower(): v for k, v in data.get("user_agents", {}).items()},
            allowed_origin=str(server.get("allowed_origin", DEFAULT_ALLOWED_ORIGIN)),
            ports=ports,
            open_browser=bool(server.get("open_browser", False)),
        )
