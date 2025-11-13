"""Type definitions for debugwand."""

from dataclasses import dataclass


@dataclass
class PodInfo:
    name: str
    namespace: str
    node_name: str
    status: str
    labels: dict[str, str]
    creation_time: str  # ISO 8601 timestamp from metadata.creationTimestamp


@dataclass
class ProcessInfo:
    pid: int
    user: str
    cpu_percent: float
    mem_percent: float
    command: str
