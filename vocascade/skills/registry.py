"""
vocascade/skills/registry.py — Declarative Skill Registry and discovery helper.
"""

import os
import sys
import logging
import inspect
import importlib.util
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger("vocascade.skills.registry")

@dataclass
class Skill:
    """A registered skill handler metadata and implementation."""
    name: str
    handler: Any  # async def (intent, entities, ctx) -> str or Stream
    examples: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    confidence: Optional[Callable[[str], float]] = None
    config: Dict[str, Any] = field(default_factory=dict)
    source: str = "bundled"  # "bundled" or "user"

class SkillRegistry:
    """Registry holding all registered voice skills."""

    def __init__(self):
        self.skills: Dict[str, Skill] = {}

    def register(
        self,
        name: str,
        handler: Any,
        examples: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        confidence: Optional[Callable[[str], float]] = None,
        config: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None
    ) -> None:
        """
        Registers a new skill. Raises ValueError if the name is already registered.
        """
        if name in self.skills:
            raise ValueError(f"Duplicate skill registration: skill name '{name}' is already registered.")

        # Auto-detect source if not explicitly provided
        if not source:
            source = "bundled"
            frame = inspect.currentframe()
            caller_frame = frame.f_back
            while caller_frame:
                mod_name = caller_frame.f_globals.get("__name__", "")
                if mod_name and mod_name not in ("vocascade.skills.registry", "vocascade.skills"):
                    if "user_skills" in mod_name or mod_name.startswith("user_skills"):
                        source = "user"
                    break
                caller_frame = caller_frame.f_back

        skill_obj = Skill(
            name=name,
            handler=handler,
            examples=examples or [],
            keywords=keywords or [],
            confidence=confidence,
            config=config or {},
            source=source
        )
        self.skills[name] = skill_obj
        logger.info(f"Registered skill '{name}' (source: {source})")

    def configure(self, skills_config: Optional[Dict[str, Any]]) -> None:
        """Apply per-skill config after discovery (US6 / FR-023): drop skills
        disabled in config (so they don't participate in routing) and attach each
        kept skill's config block."""
        skills_config = skills_config or {}
        for name in list(self.skills):
            cfg = skills_config.get(name, {}) or {}
            if cfg.get("enabled", True) is False:
                del self.skills[name]
                logger.info("Skill '%s' disabled by config — unregistered", name)
            else:
                self.skills[name].config = cfg

    def get_skill(self, name: str) -> Optional[Skill]:
        """Retrieve a registered skill by name."""
        return self.skills.get(name)

    def get_all_skills(self) -> List[Skill]:
        """Retrieve all registered skills."""
        return list(self.skills.values())

    def clear(self):
        """Clear the registry. (Useful for tests)."""
        self.skills.clear()

    def discover_bundled_skills(self):
        """Dynamically imports and registers all bundled skills in vocascade/skills/base_skills."""
        import pkgutil
        import importlib

        try:
            # We import base_skills relative to vocascade.skills
            import vocascade.skills.base_skills as base_skills
        except ImportError:
            logger.warning("No vocascade.skills.base_skills package found.")
            return

        for _, module_name, _ in pkgutil.iter_modules(base_skills.__path__, base_skills.__name__ + "."):
            try:
                importlib.import_module(module_name)
                logger.info(f"Loaded bundled skill module: {module_name}")
            except Exception as e:
                logger.error(f"Failed to load bundled skill module {module_name}: {e}", exc_info=True)

    def discover_user_skills(self, user_skills_dir: str = "user_skills"):
        """
        Dynamically imports and registers all user skills from user_skills_dir.
        Provides import isolation (errors in one file won't abort startup/other imports).
        """
        if not os.path.exists(user_skills_dir):
            logger.info(f"User skills directory '{user_skills_dir}' does not exist. Skipping.")
            return

        abs_dir = os.path.abspath(user_skills_dir)
        if abs_dir not in sys.path:
            sys.path.insert(0, abs_dir)

        for entry in os.scandir(abs_dir):
            if entry.is_file() and entry.name.endswith(".py") and not entry.name.startswith("_"):
                module_name = entry.name[:-3]
                full_mod_name = f"user_skills.{module_name}"
                try:
                    spec = importlib.util.spec_from_file_location(full_mod_name, entry.path)
                    if spec and spec.loader:
                        mod = importlib.util.module_from_spec(spec)
                        sys.modules[full_mod_name] = mod
                        spec.loader.exec_module(mod)
                        logger.info(f"Loaded user skill: {module_name}")
                except Exception as e:
                    logger.error(f"Failed to import user skill file {entry.name}: {e}", exc_info=True)

# Process-wide singleton registry instance
registry = SkillRegistry()
