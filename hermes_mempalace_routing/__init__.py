from importlib.metadata import version as _pkg_version

from .config import STANDARD_ROOMS, RoutingConfig, project_room
from .models import (
    ConflictRecord,
    ContextBudget,
    DoctorReport,
    InjectedEvidence,
    MemoryEnvelope,
    RawArtifact,
    RawDiagnosticExcerpt,
    RouteCandidate,
    RouteRun,
    StorageStats,
)
from .host_hooks import HermesHostHooks
from .plugin import HermesMemPalaceRoutingPlugin
from .storage import (
    IndexCorruptionError,
    StorageBackend,
    StorageError,
    StorageReadError,
    StorageWriteError,
    UnsupportedStorageOperation,
    create_storage,
)

try:
    __version__ = _pkg_version("hermes-mempalace-routing")
except Exception:  # pragma: no cover - local checkout without install
    __version__ = "0.1.0"

__all__ = [
    "STANDARD_ROOMS",
    "ConflictRecord",
    "ContextBudget",
    "DoctorReport",
    "HermesHostHooks",
    "HermesMemPalaceRoutingPlugin",
    "IndexCorruptionError",
    "InjectedEvidence",
    "MemoryEnvelope",
    "RawArtifact",
    "RawDiagnosticExcerpt",
    "RouteCandidate",
    "RouteRun",
    "RoutingConfig",
    "StorageBackend",
    "StorageError",
    "StorageReadError",
    "StorageStats",
    "StorageWriteError",
    "UnsupportedStorageOperation",
    "create_storage",
    "project_room",
    "__version__",
]
