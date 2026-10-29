"""Filesystem configuration shared by the tools and root schema."""

from yoreni.config_base import Base


class FileToolsConfig(Base):
    """Filesystem tools configuration."""

    enable: bool = True
