"""Jarvis identity and relationship tracking subsystem."""

from core.identity.jarvis_self import JarvisSelf
from core.identity.relationship import RelationshipTracker

__all__ = ["JarvisSelf", "RelationshipTracker"]


if __name__ == "__main__":
    print("Identity package loaded.")
