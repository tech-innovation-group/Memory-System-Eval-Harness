"""Dynamic evaluator for memory recall testing.

This module provides the MemoryDynamicEvaluator class for generating
background memories and user queries in dynamic (LLM-generated) mode.

Now supports loading prompts from YAML configuration files based on
RealUserSim, IntellAgent, AgentProcessBench, RigorBench, and MemOps papers.

Usage:
    from dynamic.simulator import MemoryDynamicEvaluator, get_evaluator, create_evaluator

    # Create a new evaluator with custom config
    evaluator = MemoryDynamicEvaluator({
        "user_simulator_config": "realistic",
        "evaluator_config": "memory_focused",
        ...
    })
    memories = evaluator.generate_background_memories()

    # Or use the global registry
    evaluator_id = create_evaluator(config)
    evaluator = get_evaluator(evaluator_id)
    query = evaluator.generate_next_query(context)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any


from dynamic import model_client as llm
from dynamic.prompt_config import (
    load_user_simulator_config,
    load_evaluator_config,
    get_prompt_template,
    list_available_simulators,
    list_available_evaluators,
)


def _validate_user_simulator_config(config: dict[str, Any]) -> None:
    """Validate user simulator configuration.

    Validates:
    - persona_prompt field exists
    - persona_prompt contains required placeholders: {background_facts}, {conversation_history}, {round_index}, {is_new_session}
    - background_memories_prompt field exists
    - background_memories_prompt contains {num_memories} placeholder

    Raises:
        ValueError: If validation fails
    """
    persona_prompt = config.get("persona_prompt", "")
    if not persona_prompt:
        raise ValueError(
            "user_simulator_config must contain 'persona_prompt' field. "
            "Please provide a user_simulator_config_yaml file with persona_prompt."
        )

    # Check for required placeholders in persona_prompt
    required_placeholders = ["{background_facts}", "{conversation_history}", "{round_index}", "{is_new_session}"]
    missing = [p for p in required_placeholders if p not in persona_prompt]
    if missing:
        raise ValueError(
            f"persona_prompt must contain placeholders: {', '.join(missing)}. "
            f"These placeholders are used to inject context during query generation."
        )

    # background_memories_prompt is required
    bg_prompt = config.get("background_memories_prompt", "")
    if not bg_prompt:
        raise ValueError(
            "user_simulator_config must contain 'background_memories_prompt' field. "
            "This prompt is used to generate background memories."
        )

    if "{num_memories}" not in bg_prompt:
        raise ValueError(
            "background_memories_prompt must contain '{num_memories}' placeholder. "
            "This placeholder is used to specify the number of memories to generate."
        )


def _validate_evaluator_config(config: dict[str, Any]) -> None:
    """Validate evaluator configuration.
    
    Validates:
    - dimensions field exists and is a non-empty list
    - Each dimension has: name, display_name, max_score
    - evaluate_prompt field exists and contains required placeholders
    
    Raises:
        ValueError: If validation fails
    """
    # dimensions is required
    dimensions = config.get("dimensions", [])
    if not dimensions:
        raise ValueError(
            "evaluator_config must contain 'dimensions' field with at least one dimension. "
            "Example: dimensions: [{name: 'fact_coverage_score', display_name: '事实覆盖', max_score: 40}]"
        )
    
    if not isinstance(dimensions, list):
        raise ValueError("'dimensions' must be a list of dimension objects.")
    
    # Validate each dimension
    for i, dim in enumerate(dimensions):
        if not isinstance(dim, dict):
            raise ValueError(f"Dimension {i} must be an object with 'name', 'display_name', 'max_score'.")
        
        required_fields = ["name", "display_name", "max_score"]
        missing = [f for f in required_fields if f not in dim]
        if missing:
            raise ValueError(
                f"Dimension {i} is missing required fields: {', '.join(missing)}. "
                f"Each dimension needs: name, display_name, max_score."
            )
    
    # evaluate_prompt is required
    eval_prompt = config.get("evaluate_prompt", "")
    if not eval_prompt:
        raise ValueError(
            "evaluator_config must contain 'evaluate_prompt' field. "
            "See configs/custom/evaluator_template.yaml for an example."
        )
    
    # Check for required placeholders in evaluate_prompt
    required_placeholders = ["{query}", "{reply}", "{ground_facts}", "{recalled_memories}", "{dimension_criteria}"]
    missing = [p for p in required_placeholders if p not in eval_prompt]
    if missing:
        raise ValueError(
            f"evaluate_prompt must contain placeholders: {', '.join(missing)}. "
            f"These placeholders are used to inject evaluation context."
        )


# ---------------------------------------------------------------------------
# Global evaluator registry
# ---------------------------------------------------------------------------

_EVALUATOR_LOCK = threading.Lock()
_EVALUATORS: dict[str, "MemoryDynamicEvaluator"] = {}
_EVALUATOR_TTL_SECONDS = 3600  # 1 hour

# Global stop flags for evaluators (evaluator_id -> bool)
_EVALUATOR_STOP_FLAGS: dict[str, bool] = {}


def set_evaluator_stopped(evaluator_id: str, stopped: bool = True) -> None:
    """Set the stop flag for an evaluator."""
    with _EVALUATOR_LOCK:
        _EVALUATOR_STOP_FLAGS[evaluator_id] = stopped


def is_evaluator_stopped(evaluator_id: str) -> bool:
    """Check if an evaluator has been stopped."""
    with _EVALUATOR_LOCK:
        return _EVALUATOR_STOP_FLAGS.get(evaluator_id, False)


def clear_evaluator_stop_flag(evaluator_id: str) -> None:
    """Clear the stop flag for an evaluator."""
    with _EVALUATOR_LOCK:
        _EVALUATOR_STOP_FLAGS.pop(evaluator_id, None)


def create_evaluator(config: dict[str, Any]) -> str:
    """Create a new evaluator and return its ID."""
    evaluator_id = f"eval-{uuid.uuid4().hex[:12]}"
    evaluator = MemoryDynamicEvaluator(config)
    with _EVALUATOR_LOCK:
        _EVALUATORS[evaluator_id] = evaluator
        # Ensure no stale stop flag exists for this ID (shouldn't happen with UUID, but be safe)
        _EVALUATOR_STOP_FLAGS.pop(evaluator_id, None)
    return evaluator_id


def get_evaluator(evaluator_id: str) -> "MemoryDynamicEvaluator | None":
    """Get an evaluator by ID. Auto-removes expired evaluators."""
    with _EVALUATOR_LOCK:
        evaluator = _EVALUATORS.get(evaluator_id)
        if evaluator is None:
            return None
        if time.time() - evaluator.created_at > _EVALUATOR_TTL_SECONDS:
            del _EVALUATORS[evaluator_id]
            _EVALUATOR_STOP_FLAGS.pop(evaluator_id, None)
            return None
        return evaluator


def remove_evaluator(evaluator_id: str) -> bool:
    """Remove an evaluator by ID."""
    with _EVALUATOR_LOCK:
        if evaluator_id in _EVALUATORS:
            del _EVALUATORS[evaluator_id]
            _EVALUATOR_STOP_FLAGS.pop(evaluator_id, None)  # Also clear stop flag
            return True
        return False


def get_available_simulators() -> list[dict[str, Any]]:
    """List all available user simulator configurations."""
    return list_available_simulators()


def get_available_evaluators() -> list[dict[str, Any]]:
    """List all available evaluator configurations."""
    return list_available_evaluators()


def list_evaluators() -> list[dict[str, Any]]:
    """List all active evaluators."""
    with _EVALUATOR_LOCK:
        return [
            {"id": eid, "created_at": ev.created_at, "mode": ev.mode, "theme": ev.theme}
            for eid, ev in _EVALUATORS.items()
        ]


# ---------------------------------------------------------------------------
# Theme pools
# ---------------------------------------------------------------------------

THEME_POOL = [
    "职场与项目管理",
    "旅行规划与出行",
    "健康管理与就医",
    "学习与考试备考",
    "社交活动与聚会",
    "购物与消费决策",
    "烹饪与饮食计划",
    "运动与健身安排",
    "宠物养护与训练",
    "家庭财务与投资",
    "子女教育与成长",
    "房屋维修与改造",
    "汽车保养与驾驶",
    "园艺与种植",
    "摄影与创作",
    "志愿活动与社区服务",
]


# ---------------------------------------------------------------------------
# MemoryDynamicEvaluator class
# ---------------------------------------------------------------------------

class MemoryDynamicEvaluator:
    """Dynamic evaluator for generating test scenarios and queries.
    
    Supports loading prompts from YAML configuration files for:
    - User simulator behavior (persona, interaction mode, communication style)
    - Evaluation prompts (response quality, memory recall, step evaluation)
    
    Config keys for prompt loading:
        - user_simulator_config: Name of user simulator config (e.g., "default", "realistic", "difficult")
        - evaluator_config: Name of evaluator config (e.g., "default", "memory_focused")
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize the evaluator.

        Args:
            config: Configuration dict with keys:
                - num_memories: Number of memories to generate
                - theme: Theme for generated memories
                - custom_scenario: Custom scenario text (skip LLM generation if provided)
                - llm_config: LLM configuration (model, base_url, api_key)
                - user_simulator_config: Name of user simulator config file
                - evaluator_config: Name of evaluator config file
        """
        self.config = config
        self.theme = config.get("theme", "")
        self.custom_scenario = config.get("custom_scenario", "")
        self.parsed_memories = config.get("parsed_memories", [])
        self.created_at = time.time()

        self.background_memories: list[dict[str, Any]] = []
        self.conversation_history: list[dict[str, Any]] = []
        self.generated_queries: list[dict[str, Any]] = []
        self._memories_generated = False

        # LLM config
        llm_config = config.get("llm_config", {})
        self.model = llm_config.get("model", "deepseek-v4-flash-0731")
        self.base_url = llm_config.get("base_url") or None
        self.api_key = llm_config.get("api_key") or None
        
        # Load prompt configurations
        self.user_simulator_config: dict[str, Any] = {}
        self.evaluator_config: dict[str, Any] = {}
        
        # Support both config name and direct YAML content
        user_sim_config_yaml = config.get("user_simulator_config_yaml", "")
        user_sim_config_name = config.get("user_simulator_config", "")
        if user_sim_config_yaml:
            import yaml
            try:
                self.user_simulator_config = yaml.safe_load(user_sim_config_yaml) or {}
                print(f"[DynamicEvaluator] Loaded user simulator config from YAML content")
            except Exception as e:
                raise ValueError(f"Failed to parse user_simulator_config_yaml: {e}")
        elif user_sim_config_name:
            try:
                self.user_simulator_config = load_user_simulator_config(user_sim_config_name)
                print(f"[DynamicEvaluator] Loaded user simulator config: {user_sim_config_name}")
            except FileNotFoundError as e:
                raise ValueError(f"User simulator config not found: {e}")
        
        eval_config_yaml = config.get("evaluator_config_yaml", "")
        eval_config_name = config.get("evaluator_config", "")
        if eval_config_yaml:
            import yaml
            try:
                self.evaluator_config = yaml.safe_load(eval_config_yaml) or {}
                print(f"[DynamicEvaluator] Loaded evaluator config from YAML content")
            except Exception as e:
                raise ValueError(f"Failed to parse evaluator_config_yaml: {e}")
        elif eval_config_name:
            try:
                self.evaluator_config = load_evaluator_config(eval_config_name)
                print(f"[DynamicEvaluator] Loaded evaluator config: {eval_config_name}")
            except FileNotFoundError as e:
                raise ValueError(f"Evaluator config not found: {e}")
        
        # Validate configurations
        _validate_user_simulator_config(self.user_simulator_config)
        if not self.evaluator_config:
            raise ValueError(
                "evaluator_config is required. "
                "Please provide an evaluator_config_yaml file with 'dimensions' and 'evaluate_prompt' fields. "
                "See configs/custom/evaluator_template.yaml for an example."
            )
        _validate_evaluator_config(self.evaluator_config)
        
        # Parse evaluation dimensions from config
        self.eval_dimensions: list[dict[str, Any]] = []
        if "dimensions" in self.evaluator_config:
            self.eval_dimensions = self.evaluator_config["dimensions"]
            total_max = sum(d.get("max_score", 0) for d in self.eval_dimensions)
            print(f"[DynamicEvaluator] Loaded {len(self.eval_dimensions)} dimensions, total max score: {total_max}")
        else:
            # This should be caught by _validate_evaluator_config, but check anyway
            raise ValueError(
                "evaluator_config must contain 'dimensions' field. "
                "See configs/custom/evaluator_template.yaml for an example."
            )
        
        # Debug log
        print(f"[DynamicEvaluator] Config received: num_memories={config.get('num_memories')}, theme={self.theme}")
        print(f"[DynamicEvaluator] LLM config: model={self.model}, base_url={self.base_url}, api_key={'***' if self.api_key else 'None'}")

    def generate_background_memories(self) -> dict[str, Any]:
        """Generate or load background memories.

        Returns:
            {
                "memories": [...],
                "theme": str,
            }
        """
        if self._memories_generated:
            return {
                "memories": self.background_memories,
                "theme": self.theme,
            }

        # Priority: parsed_memories > custom_scenario > dynamic generation
        if self.parsed_memories:
            self.background_memories = [
                {"id": m.get("id", f"f{i+1}"), "text": m.get("text", "")}
                for i, m in enumerate(self.parsed_memories)
                if m.get("text")
            ]
            if not self.theme:
                self.theme = "自定义场景"
        elif self.custom_scenario:
            self.background_memories = self._generate_memories_from_custom_scenario()
        else:
            self.background_memories = self._generate_dynamic_memories()

        self._memories_generated = True
        return {
            "memories": self.background_memories,
            "theme": self.theme,
        }

    def _generate_memories_from_custom_scenario(self) -> list[dict[str, Any]]:
        """Generate memories from custom scenario text.

        This is used when custom_scenario is provided directly from the frontend.
        """
        self.theme = "自定义场景"
        # Split custom scenario into memory-like chunks
        # Treat each sentence or paragraph as a memory
        sentences = re.split(r'[。\n]', self.custom_scenario)
        memories = []
        for i, sentence in enumerate(sentences):
            sentence = sentence.strip()
            if sentence and len(sentence) > 5:
                memories.append({
                    "id": f"f{i+1}",
                    "text": sentence,
                    "length_hint": "short" if len(sentence) < 50 else "medium" if len(sentence) < 100 else "long",
                })
        return memories if memories else self._generate_fallback_memories()

    def _generate_dynamic_memories(self) -> list[dict[str, Any]]:
        """Generate memories using LLM."""
        # Otherwise, generate memories using LLM
        num_memories = self.config.get("num_memories", 10)
        
        # Must have background_memories_prompt from config
        bg_prompt_template = self.user_simulator_config.get("background_memories_prompt")
        if not bg_prompt_template:
            raise ValueError(
                "background_memories_prompt is required in user_simulator_config. "
                "Please provide a user_simulator_config_yaml file with background_memories_prompt field."
            )
        # Replace {num_memories} placeholder (avoid .format() which parses all curly braces)
        prompt = bg_prompt_template.replace("{num_memories}", str(num_memories))
        
        # Set theme from config or use default
        if not self.theme:
            self.theme = "职场项目开发"  # Default theme matching the config

        try:
            result = llm.openai_chat(
                messages=[
                    {"role": "system", "content": "You are a test scenario generator. Output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
                temperature=0.9,
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=120,
            )

            if "error" in result:
                print(f"[DynamicEvaluator] LLM error: {result.get('error')}")
                return self._generate_fallback_memories()

            answer = result.get("answer", "")
            print(f"[DynamicEvaluator] LLM answer length: {len(answer)}, model: {self.model}, base_url: {self.base_url}")
            json_match = re.search(r"\[[\s\S]*\]", answer)
            if not json_match:
                print(f"[DynamicEvaluator] No JSON array found in answer")
                return self._generate_fallback_memories()

            memories = json.loads(json_match.group())
            print(f"[DynamicEvaluator] Parsed {len(memories)} memories from LLM")

            # Validate structure
            validated = []
            for i, m in enumerate(memories):
                if isinstance(m, dict) and m.get("text"):
                    validated.append({
                        "id": m.get("id") or f"f{i+1}",
                        "text": m.get("text", ""),
                        "length_hint": m.get("length_hint", "medium"),
                    })

            return validated if validated else self._generate_fallback_memories()

        except Exception as e:
            print(f"[DynamicEvaluator] Exception: {e}")
            return self._generate_fallback_memories()

    def _generate_fallback_memories(self) -> list[dict[str, Any]]:
        """Generate fallback memories when LLM or dataset is unavailable."""
        num_memories = self.config.get("num_memories", 10)
        theme = self.theme or "日常生活"
        
        # Base fallback memories
        base_memories = [
            {"id": "f1", "text": "我喜欢在周末去公园跑步，通常早上7点出发。", "length_hint": "medium"},
            {"id": "f2", "text": "我家的猫叫小白，是一只三岁的英短。", "length_hint": "short"},
            {"id": "f3", "text": "我下周三有一个重要的项目汇报，需要准备PPT。", "length_hint": "medium"},
            {"id": "f4", "text": "我最喜欢的餐厅是公司楼下那家川菜馆，水煮鱼很好吃。", "length_hint": "medium"},
            {"id": "f5", "text": "我女儿的生日是6月15日，她想要一个乐高玩具。", "length_hint": "medium"},
            {"id": "f6", "text": "我每天早上喝一杯咖啡，喜欢加牛奶不加糖。", "length_hint": "short"},
            {"id": "f7", "text": "我最近在学习Python编程，每天晚上花一小时练习。", "length_hint": "medium"},
            {"id": "f8", "text": "我家的车位在B2层03号，靠近电梯口。", "length_hint": "short"},
            {"id": "f9", "text": "我计划下个月去日本旅游，已经订好了机票和酒店。", "length_hint": "medium"},
            {"id": "f10", "text": "我儿子的班级在三年级二班，班主任是李老师。", "length_hint": "short"},
            {"id": "f11", "text": "我每周三晚上有瑜伽课，在小区对面的健身房。", "length_hint": "medium"},
            {"id": "f12", "text": "我最喜欢的电影是《肖申克的救赎》，看了至少五遍。", "length_hint": "medium"},
            {"id": "f13", "text": "我每天的通勤时间是40分钟，坐地铁3号线。", "length_hint": "short"},
            {"id": "f14", "text": "我最近在装修房子，预计下个月完工。", "length_hint": "short"},
            {"id": "f15", "text": "我习惯用印象笔记记录工作事项，已经用了三年了。", "length_hint": "medium"},
        ]
        
        # Return requested number of memories
        return base_memories[:num_memories]

    def _pick_random_theme(self) -> str:
        """Pick a random theme from the pool."""
        import random
        return random.choice(THEME_POOL)

    def generate_next_query(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate the next user query.

        Args:
            context: Context dict with keys:
                - round_index: Current round number
                - previous_queries: List of previous queries
                - previous_replies: List of previous replies
                - is_new_session: Whether this is a new session

        Returns:
            {
                "query": str,
                "ground_facts": list[str],
                "complexity": str,
                "reasoning": str,
                "new_session_hint": bool,
            }
        """
        if not self._memories_generated:
            self.generate_background_memories()

        return self._generate_dynamic_query(context)

    def _generate_dynamic_query(self, context: dict[str, Any]) -> dict[str, Any]:
        """Generate query using LLM."""
        round_index = context.get("round_index", 0)
        previous_queries = context.get("previous_queries", [])
        previous_replies = context.get("previous_replies", [])
        is_new_session = context.get("is_new_session", False)

        # Build facts text
        facts_text = "\n".join(
            f"- [{m['id']}] {m['text']}"
            for m in self.background_memories[:10]  # Limit for prompt length
        )

        # Build history text — accumulate from most recent round backwards
        # until approximately 64K tokens (≈256K chars at ~4 chars/token) is reached
        _MAX_HISTORY_CHARS = 256_000
        history_parts: list[str] = []
        total_chars = 0
        for i in range(len(previous_queries) - 1, -1, -1):
            q = previous_queries[i]
            r = previous_replies[i] if i < len(previous_replies) else ""
            entry = f"User: {q}\nAssistant: {r}"
            if total_chars + len(entry) > _MAX_HISTORY_CHARS:
                break
            history_parts.append(entry)
            total_chars += len(entry)
        history_parts.reverse()
        history_text = "\n".join(history_parts) if history_parts else "(No previous conversation)"

        # Use persona_prompt from user_simulator_config - REQUIRED for dynamic mode
        persona_prompt = self.user_simulator_config.get("persona_prompt", "")
        
        if not persona_prompt:
            raise ValueError(
                "persona_prompt is required in user_simulator_config for dynamic mode. "
                "Please provide a user_simulator_config_yaml file with persona_prompt field."
            )
        
        # Use placeholder-based replacement (placeholders validated in __init__)
        full_prompt = persona_prompt.format(
            background_facts=facts_text,
            conversation_history=history_text,
            round_index=round_index + 1,
            is_new_session=str(is_new_session),
        )

        try:
            result = llm.openai_chat(
                messages=[
                    {"role": "system", "content": "You are a test query generator. Output only valid JSON."},
                    {"role": "user", "content": full_prompt},
                ],
                model=self.model,
                temperature=0.7,
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60,
            )

            if "error" in result:
                return self._fallback_query(round_index)

            answer = result.get("answer", "")
            json_match = re.search(r"\{[\s\S]*\}", answer)
            if not json_match:
                return self._fallback_query(round_index)

            query_data = json.loads(json_match.group())

            # Validate structure
            return {
                "query": query_data.get("query", "你还记得什么？"),
                "ground_facts": query_data.get("ground_facts", []),
                "complexity": query_data.get("complexity", "simple"),
                "reasoning": query_data.get("reasoning", ""),
                "new_session_hint": query_data.get("new_session_hint", False),
            }

        except Exception:
            return self._fallback_query(round_index)

    def _fallback_query(self, round_index: int) -> dict[str, Any]:
        """Generate fallback query when LLM is unavailable."""
        if round_index < len(self.background_memories):
            fact = self.background_memories[round_index]
            return {
                "query": f"我刚才告诉你的那个关于{fact['text'][:15]}的事是什么？",
                "ground_facts": [fact["id"]],
                "complexity": "simple",
                "reasoning": "Fallback simple recall",
                "new_session_hint": False,
            }

        # Cross-session recall test
        import random
        fact = random.choice(self.background_memories) if self.background_memories else {"id": "f1", "text": "测试事实"}
        return {
            "query": "你能告诉我之前我说过的一些事情吗？",
            "ground_facts": [fact["id"]],
            "complexity": "medium",
            "reasoning": "Fallback cross-session recall",
            "new_session_hint": True,
        }

    def add_to_history(self, query: str, reply: str) -> None:
        """Add a query-reply pair to conversation history."""
        self.conversation_history.append({
            "query": query,
            "reply": reply,
            "timestamp": time.time(),
        })

    def get_state(self) -> dict[str, Any]:
        """Get the current state of the evaluator."""
        return {
            "theme": self.theme,
            "num_background_memories": len(self.background_memories),
            "num_conversation_turns": len(self.conversation_history),
            "memories_generated": self._memories_generated,
            "created_at": self.created_at,
        }

    def evaluate_response(
        self,
        query: str,
        reply: str,
        ground_facts: list[dict[str, Any] | str],
        recalled_memories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Evaluate the quality of an AI response.

        Args:
            query: The user query
            reply: The AI response to evaluate
            ground_facts: Expected facts (can be IDs like "f1" or objects with "text")
            recalled_memories: Memories that were recalled during inference

        Returns:
            {
                "score": int (0-100),
                "reason": str,
                "matched_facts": int,
                "total_facts": int,
                "recall_helped": bool,
                "details": list,
            }
        """
        if not self._memories_generated:
            self.generate_background_memories()

        # Build ground facts text for prompt
        ground_facts_text = ""
        fact_details = []
        
        if ground_facts:
            for fact_item in ground_facts:
                if isinstance(fact_item, str):
                    # It's a fact ID, look up the full fact
                    fact_id = fact_item
                    found = False
                    for m in self.background_memories:
                        if m.get("id") == fact_id:
                            fact_details.append(f"- [{fact_id}] {m.get('text', '')}")
                            found = True
                            break
                    if not found:
                        # Use the ID as-is if not found
                        fact_details.append(f"- [{fact_id}] (未找到)")
                elif isinstance(fact_item, dict):
                    # It's a fact object
                    fact_id = fact_item.get("id", "?")
                    text = fact_item.get("text", "") or fact_item.get("fact", "") or str(fact_item)
                    fact_details.append(f"- [{fact_id}] {text[:200]}")
            
            ground_facts_text = "\n".join(fact_details) if fact_details else "(无预设事实)"
        else:
            ground_facts_text = "(无预设事实)"

        # Build recalled memories text
        recalled_text = ""
        if recalled_memories:
            recalled_parts = []
            for i, mem in enumerate(recalled_memories[:5]):  # Limit to 5
                text = mem.get("text", "") or mem.get("query", "") or str(mem)
                if text:
                    recalled_parts.append(f"- {text[:200]}")
            recalled_text = "\n".join(recalled_parts) if recalled_parts else "(无召回记忆)"
        else:
            recalled_text = "(无召回记忆)"

        # Use custom prompt from evaluator_config - REQUIRED
        if not self.evaluator_config:
            raise ValueError(
                "evaluator_config is required for response evaluation. "
                "Please provide an evaluator_config_yaml file with 'evaluate_prompt' and 'dimensions' fields."
            )
        
        eval_prompt_template = None
        if "evaluate_prompt" in self.evaluator_config:
            custom_prompt = self.evaluator_config["evaluate_prompt"]
            if isinstance(custom_prompt, str) and custom_prompt.strip():
                eval_prompt_template = custom_prompt
                print(f"[DynamicEvaluator] Using evaluate_prompt from config")
        
        if not eval_prompt_template:
            raise ValueError(
                "evaluator_config must contain 'evaluate_prompt' field. "
                "See configs/custom/evaluator_template.yaml for an example."
            )

        # Build format args - support both default and custom placeholder names
        # Generate dimension criteria text if dimensions are defined
        dimension_criteria = ""
        if self.eval_dimensions:
            criteria_parts = []
            for i, dim in enumerate(self.eval_dimensions, 1):
                name = dim.get("name", "")
                display_name = dim.get("display_name", name)
                max_score = dim.get("max_score", 0)
                desc = dim.get("description", "")
                criteria_parts.append(f"### {i}. {display_name} (0-{max_score} points)\n{desc}")
            dimension_criteria = "\n\n".join(criteria_parts)
        else:
            dimension_criteria = "Use your judgment to evaluate the response quality."

        format_args = {
            "query": query,
            "reply": reply,
            "ground_facts": ground_facts_text,
            "recalled_memories": recalled_text,
            "dimension_criteria": dimension_criteria,
        }

        prompt = eval_prompt_template.format(**format_args)

        try:
            result = llm.openai_chat(
                messages=[
                    {"role": "system", "content": "You are a response quality evaluator. Output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
                temperature=0.3,
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60,
            )

            if "error" in result:
                print(f"[DynamicEvaluator] LLM error: {result.get('error')}")
                return self._fallback_evaluate(query, reply, ground_facts, recalled_memories)

            answer = result.get("answer", "")
            json_match = re.search(r"\{[\s\S]*\}", answer)
            if not json_match:
                print(f"[DynamicEvaluator] No JSON found in answer: {answer[:200]}")
                return self._fallback_evaluate(query, reply, ground_facts, recalled_memories)

            eval_data = json.loads(json_match.group())

            # Validate and normalize total score
            score = eval_data.get("score", 50)
            if not isinstance(score, (int, float)):
                score = 50
            score = max(0, min(100, int(score)))

            # Dynamically extract dimension scores based on config
            dimension_scores = {}
            for dim in self.eval_dimensions:
                dim_name = dim.get("name", "")
                max_val = dim.get("max_score", 100)
                if dim_name:
                    val = eval_data.get("dimension_scores", {}).get(dim_name, eval_data.get(dim_name, 0))
                    if not isinstance(val, (int, float)):
                        val = 0
                    dimension_scores[dim_name] = max(0, min(max_val, int(val)))

            # If no dimension_scores in response, compute from total score proportionally
            if not dimension_scores and self.eval_dimensions:
                total_max = sum(d.get("max_score", 0) for d in self.eval_dimensions)
                if total_max > 0:
                    ratio = score / 100
                    for dim in self.eval_dimensions:
                        dim_name = dim.get("name", "")
                        max_val = dim.get("max_score", 0)
                        if dim_name:
                            dimension_scores[dim_name] = int(max_val * ratio)

            # Additional metadata
            matched_facts = eval_data.get("matched_facts", 0)
            total_facts = len(ground_facts) if ground_facts else 0
            recall_helped = eval_data.get("recall_helped", False)
            hallucination_detected = eval_data.get("hallucination_detected", False)
            task_completed = eval_data.get("task_completed", False)
            strengths = eval_data.get("strengths", [])
            weaknesses = eval_data.get("weaknesses", [])

            # Build dimension_info for frontend display
            dimension_info = {}
            for dim in self.eval_dimensions:
                dim_name = dim.get("name", "")
                if dim_name:
                    dimension_info[dim_name] = {
                        "display_name": dim.get("display_name", dim_name),
                        "max_score": dim.get("max_score", 100),
                    }

            return {
                "score": score,
                "dimension_scores": dimension_scores,
                "dimension_info": dimension_info,
                "reason": eval_data.get("reason", ""),
                "matched_facts": matched_facts,
                "total_facts": total_facts,
                "recall_helped": recall_helped,
                "hallucination_detected": hallucination_detected,
                "task_completed": task_completed,
                "strengths": strengths,
                "weaknesses": weaknesses,
                "details": eval_data.get("details", []),
            }

        except Exception:
            return self._fallback_evaluate(query, reply, ground_facts, recalled_memories)

    def _fallback_evaluate(
        self,
        query: str,
        reply: str,
        ground_facts: list[dict[str, Any] | str],
        recalled_memories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Fallback evaluation when LLM is unavailable."""
        # Simple keyword matching fallback
        reply_lower = reply.lower()
        matched = 0
        total = len(ground_facts) if ground_facts else 0

        for fact_item in ground_facts:
            # Get fact text - handle both ID strings and objects
            if isinstance(fact_item, str):
                # Look up fact by ID
                fact_text = ""
                for m in self.background_memories:
                    if m.get("id") == fact_item:
                        fact_text = m.get("text", "")
                        break
                if not fact_text:
                    fact_text = fact_item
            elif isinstance(fact_item, dict):
                fact_text = fact_item.get("text", "") or fact_item.get("fact", "") or str(fact_item)
            else:
                fact_text = str(fact_item)
            
            if not fact_text:
                continue
            # Check if key terms from fact appear in reply
            keywords = [w for w in fact_text.lower().split() if len(w) >= 2][:5]
            if sum(1 for kw in keywords if kw in reply_lower) >= len(keywords) * 0.5:
                matched += 1

        score = int((matched / total) * 100) if total > 0 else 50
        recall_helped = len(recalled_memories) > 0

        # Calculate individual scores based on matching
        fact_coverage = int((matched / total) * 40) if total > 0 else 0
        accuracy = int((matched / total) * 30) if total > 0 else 15
        relevance = 15 if matched > 0 else 5  # Default relevance
        recall_quality = 5 if recall_helped else 0

        return {
            "score": score,
            "fact_coverage_score": fact_coverage,
            "accuracy_score": accuracy,
            "relevance_score": relevance,
            "recall_quality_score": recall_quality,
            "reason": f"匹配 {matched}/{total} 事实" if total > 0 else "无预设事实",
            "matched_facts": matched,
            "total_facts": total,
            "recall_helped": recall_helped,
            "details": [],
        }
