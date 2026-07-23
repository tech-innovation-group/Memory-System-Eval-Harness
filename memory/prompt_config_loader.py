"""Configuration loader for user simulator and evaluator prompts.

This module provides functions to load and parse YAML configuration files
for user simulators and evaluators, based on the design patterns from
RealUserSim, IntellAgent, AgentProcessBench, RigorBench, and MemOps papers.

Usage:
    from memory.prompt_config_loader import (
        load_user_simulator_config,
        load_evaluator_config,
        list_available_simulators,
        list_available_evaluators,
    )
    
    # Load a specific user simulator config
    config = load_user_simulator_config("realistic")
    
    # Load evaluator config
    eval_config = load_evaluator_config("memory_focused")
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


# Default config directories
DEFAULT_USER_SIMULATOR_DIR = Path(__file__).resolve().parent.parent / "configs" / "user_simulator"
DEFAULT_EVALUATOR_DIR = Path(__file__).resolve().parent.parent / "configs" / "evaluator"
# Fallback: configs/custom/ holds templates shared across both config types
_CUSTOM_CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs" / "custom"


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict.
    
    Uses a simple YAML parser that handles basic structures.
    For full YAML support, install PyYAML.
    """
    try:
        import yaml
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Fallback to simple parser
        return _parse_simple_yaml(path)


def _parse_simple_yaml(path: Path) -> dict[str, Any]:
    """Simple YAML parser for basic structures.
    
    Handles:
    - Key: value pairs
    - Nested dicts (indentation)
    - Lists with -
    - Quoted strings
    - Numbers
    - Booleans
    """
    result: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, result)]
    current_list: list[Any] | None = None
    
    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        
        # Calculate indentation
        indent = len(line) - len(line.lstrip())
        
        # Pop stack until we find the right level
        while stack and stack[-1][0] >= indent:
            stack.pop()
        
        parent = stack[-1][1] if stack else result
        
        # Handle list items
        if stripped.startswith("- "):
            item_str = stripped[2:].strip()
            
            if isinstance(parent, list):
                parent.append(_parse_value(item_str))
            elif isinstance(parent, dict):
                # This shouldn't happen normally
                pass
            else:
                # Convert to list
                pass
            i += 1
            continue
        
        # Handle key: value
        if ":" in stripped:
            colon_pos = stripped.index(":")
            key = stripped[:colon_pos].strip()
            value_part = stripped[colon_pos + 1:].strip()
            
            if isinstance(parent, dict):
                if value_part:
                    # Inline value
                    parent[key] = _parse_value(value_part)
                else:
                    # Value on next lines (nested structure)
                    parent[key] = {} if i + 1 < len(lines) and lines[i + 1] and not lines[i + 1].strip().startswith("- ") else {}
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith("- "):
                        parent[key] = []
                        current_list = parent[key]
                    stack.append((indent, parent[key]))
        else:
            # Continuation of previous value (multiline string)
            pass
        
        i += 1
    
    return result


def _parse_value(value: str) -> Any:
    """Parse a YAML value string into Python type."""
    if not value:
        return None
    
    value = value.strip()
    
    # Handle quoted strings
    if (value.startswith('"') and value.endswith('"')) or \
       (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    
    # Handle booleans
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    
    # Handle numbers
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass
    
    # Default to string
    return value


def load_user_simulator_config(
    name: str = "default",
    config_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Load a user simulator configuration file.
    
    Args:
        name: Configuration name (without .yaml extension)
        config_dir: Custom config directory. If None, uses default.
    
    Returns:
        Configuration dict with keys:
        - name: Config name
        - version: Config version
        - description: Config description
        - persona: User persona settings
        - interaction_mode: Interaction mode settings
        - communication_style: Communication style instructions
        - prompts: Prompt templates
        - llm_config: LLM configuration
        - metadata: Additional metadata
    
    Raises:
        FileNotFoundError: If the config file doesn't exist
    """
    if config_dir is None:
        config_dir = DEFAULT_USER_SIMULATOR_DIR
    else:
        config_dir = Path(config_dir)
    
    config_path = config_dir / f"{name}.yaml"
    
    if not config_path.exists():
        # Try with .yml extension
        config_path = config_dir / f"{name}.yml"
    
    if not config_path.exists() and config_dir is DEFAULT_USER_SIMULATOR_DIR:
        # Fallback: search in configs/custom/
        for ext in (".yaml", ".yml"):
            alt = _CUSTOM_CONFIG_DIR / f"{name}{ext}"
            if alt.exists():
                config_path = alt
                break
    
    if not config_path.exists():
        raise FileNotFoundError(
            f"User simulator config not found: {name}. "
            f"Searched in: {config_dir}"
            + (f" and {_CUSTOM_CONFIG_DIR}" if config_dir is DEFAULT_USER_SIMULATOR_DIR else "")
        )
    
    config = _load_yaml(config_path)
    config["_source_path"] = str(config_path)
    
    # Resolve environment variables in LLM config
    config = _resolve_env_vars(config)
    
    return config


def load_evaluator_config(
    name: str = "default",
    config_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Load an evaluator configuration file.
    
    Args:
        name: Configuration name (without .yaml extension)
        config_dir: Custom config directory. If None, uses default.
    
    Returns:
        Configuration dict with keys:
        - name: Config name
        - version: Config version
        - description: Config description
        - evaluation_dimensions: Evaluation dimension settings
        - speed_metrics: Speed measurement settings
        - prompts: Evaluation prompt templates
        - failure_attribution: Failure analysis settings
        - llm_config: LLM configuration
        - metadata: Additional metadata
    
    Raises:
        FileNotFoundError: If the config file doesn't exist
    """
    if config_dir is None:
        config_dir = DEFAULT_EVALUATOR_DIR
    else:
        config_dir = Path(config_dir)
    
    config_path = config_dir / f"{name}.yaml"
    
    if not config_path.exists():
        # Try with .yml extension
        config_path = config_dir / f"{name}.yml"
    
    if not config_path.exists() and config_dir is DEFAULT_EVALUATOR_DIR:
        # Fallback: search in configs/custom/
        for ext in (".yaml", ".yml"):
            alt = _CUSTOM_CONFIG_DIR / f"{name}{ext}"
            if alt.exists():
                config_path = alt
                break
    
    if not config_path.exists():
        raise FileNotFoundError(
            f"Evaluator config not found: {name}. "
            f"Searched in: {config_dir}"
            + (f" and {_CUSTOM_CONFIG_DIR}" if config_dir is DEFAULT_EVALUATOR_DIR else "")
        )
    
    config = _load_yaml(config_path)
    config["_source_path"] = str(config_path)
    
    # Resolve environment variables in LLM config
    config = _resolve_env_vars(config)
    
    return config


def list_available_simulators(config_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """List all available user simulator configurations.
    
    Args:
        config_dir: Custom config directory. If None, uses default.
    
    Returns:
        List of config info dicts with keys:
        - name: Config name
        - version: Config version
        - description: Config description
        - path: Path to config file
    """
    if config_dir is None:
        config_dir = DEFAULT_USER_SIMULATOR_DIR
    else:
        config_dir = Path(config_dir)
    
    if not config_dir.exists():
        return []
    
    configs = []
    for path in config_dir.glob("*.yaml"):
        try:
            config = _load_yaml(path)
            configs.append({
                "name": config.get("name", path.stem),
                "version": config.get("version", "unknown"),
                "description": config.get("description", ""),
                "path": str(path),
                "interaction_mode": config.get("interaction_mode", "unknown"),
            })
        except Exception:
            # Skip invalid configs
            continue
    
    return configs


def list_available_evaluators(config_dir: Path | str | None = None) -> list[dict[str, Any]]:
    """List all available evaluator configurations.
    
    Args:
        config_dir: Custom config directory. If None, uses default.
    
    Returns:
        List of config info dicts with keys:
        - name: Config name
        - version: Config version
        - description: Config description
        - path: Path to config file
    """
    if config_dir is None:
        config_dir = DEFAULT_EVALUATOR_DIR
    else:
        config_dir = Path(config_dir)
    
    configs = []
    
    if config_dir.exists():
        for path in config_dir.glob("*.yaml"):
            try:
                config = _load_yaml(path)
                configs.append({
                    "name": config.get("name", path.stem),
                    "version": config.get("version", "unknown"),
                    "description": config.get("description", ""),
                    "path": str(path),
                    "dimensions": list(config.get("evaluation_dimensions", {}).keys()),
                })
            except Exception:
                continue
    
    # Also scan configs/custom/ for evaluator templates
    if config_dir is DEFAULT_EVALUATOR_DIR and _CUSTOM_CONFIG_DIR.exists():
        for path in _CUSTOM_CONFIG_DIR.glob("*evaluator*.yaml"):
            try:
                config = _load_yaml(path)
                name = config.get("name", path.stem)
                if not any(c["name"] == name for c in configs):
                    configs.append({
                        "name": name,
                        "version": config.get("version", "unknown"),
                        "description": config.get("description", ""),
                        "path": str(path),
                        "dimensions": list(config.get("evaluation_dimensions", {}).keys()),
                    })
            except Exception:
                continue
    
    return configs


def _resolve_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """Resolve environment variables in config values.
    
    Supports ${ENV_VAR} and $ENV_VAR syntax.
    Handles nested dicts recursively.
    """
    import re
    
    def resolve_value(value: Any) -> Any:
        if isinstance(value, str):
            # Match ${VAR} or $VAR
            pattern = r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)'
            
            def replacer(match):
                var_name = match.group(1) or match.group(2)
                return os.environ.get(var_name, "")
            
            return re.sub(pattern, replacer, value)
        elif isinstance(value, dict):
            return {k: resolve_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [resolve_value(item) for item in value]
        else:
            return value
    
    return resolve_value(config)


def get_prompt_template(
    config: dict[str, Any],
    template_name: str,
    **variables,
) -> str:
    """Get a rendered prompt template from config.
    
    Args:
        config: Loaded configuration dict
        template_name: Name of the template (e.g., "system_prompt", "user_response_prompt")
        **variables: Variables to substitute in the template
    
    Returns:
        Rendered prompt string
    """
    prompts = config.get("prompts", {})
    template = prompts.get(template_name, "")
    
    if not template:
        return ""
    
    # Simple variable substitution
    for key, value in variables.items():
        placeholder = "{" + key + "}"
        template = template.replace(placeholder, str(value) if value else "")
    
    return template


def merge_configs(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge two configuration dicts, with override taking precedence.
    
    Handles nested dicts recursively.
    Lists are replaced, not merged.
    """
    result = base.copy()
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value
    
    return result


# ---------------------------------------------------------------------------
# Convenience functions for common use cases
# ---------------------------------------------------------------------------

def get_user_simulator_system_prompt(
    config_name: str = "default",
    persona_description: str = "",
    interaction_mode: str = "",
    task_context: str = "",
    communication_guidelines: str = "",
) -> str:
    """Get the system prompt for a user simulator.
    
    Args:
        config_name: Name of the user simulator config
        persona_description: Description of the user persona
        interaction_mode: Interaction mode setting
        task_context: Task context for the simulation
        communication_guidelines: Communication style guidelines
    
    Returns:
        System prompt string
    """
    config = load_user_simulator_config(config_name)
    return get_prompt_template(
        config,
        "system_prompt",
        persona_description=persona_description,
        interaction_mode=interaction_mode,
        task_context=task_context,
        communication_guidelines=communication_guidelines,
    )


def get_evaluator_prompt(
    config_name: str = "default",
    template_name: str = "response_evaluation",
    **variables,
) -> str:
    """Get an evaluation prompt from config.
    
    Args:
        config_name: Name of the evaluator config
        template_name: Name of the template
        **variables: Variables to substitute
    
    Returns:
        Evaluation prompt string
    """
    config = load_evaluator_config(config_name)
    return get_prompt_template(config, template_name, **variables)