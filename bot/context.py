"""Context engine: recipe -> assembled prompt.

Reads YAML state + strategy + optional Grimoire RAG,
assembles a system prompt via Jinja2 template.
"""

import logging
import os
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger("cos.context")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RECIPES_DIR = BASE_DIR / "recipes"
TEMPLATES_DIR = BASE_DIR / "templates"


def load_yaml(filename: str) -> dict:
    """Load a YAML file from data/ directory."""
    path = DATA_DIR / filename
    if not path.exists():
        logger.warning(f"YAML not found: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_recipe(recipe_name: str) -> str:
    """Load a recipe .md file from recipes/ directory."""
    path = RECIPES_DIR / f"{recipe_name}.md"
    if not path.exists():
        logger.warning(f"Recipe not found: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def build_system_prompt(**kwargs) -> str:
    """Build system prompt from Jinja2 template + context data."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("system_prompt.md")
    return template.render(**kwargs)


def assemble_context(recipe_name: str) -> dict:
    """Load all context needed for a given recipe.

    Returns dict with keys: strategy, intents, goals, user_model, recipe_instruction.
    """
    context = {
        "strategy": load_yaml("strategy.yaml"),
        "intents": load_yaml("intents.yaml"),
        "goals": load_yaml("goals.yaml"),
        "user_model": load_yaml("user_model.yaml"),
        "recipe_instruction": load_recipe(recipe_name),
    }
    return context
