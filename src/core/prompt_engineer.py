"""Prompt Engineer — transforms structured user requirements into production-grade AI prompts.

This module is used by the Prompt Studio feature. It wraps the existing Anthropic
and Bedrock clients (from AgentSystemExecutor) and sends a carefully-crafted
meta-prompt to generate 3 DISTINCT variant prompts in a single LLM call.

Each variant uses a different approach:
  - Variant 1: Structured XML (heavy <context>, <task>, <output_format> tags)
  - Variant 2: Conversational Markdown (natural prose with headers)
  - Variant 3: Concise Imperative (terse, action-oriented)

Supports two operations:
  - generate_variants(): initial generation from structured inputs
  - refine_variants(): iterate on a selected variant using user feedback
"""

import json
import re
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger()

# Bedrock model ID — same default as executor.py
BEDROCK_MODEL_DEFAULT = "anthropic.claude-sonnet-4-20250514-v1:0"


class PromptEngineerError(Exception):
    """Raised when the LLM fails to return a parseable response."""


META_SYSTEM_PROMPT = """You are a world-class prompt engineering expert. You transform user requirements into production-ready AI prompts that follow established best practices.

Given the user's structured input, produce EXACTLY 3 DISTINCT variant prompts. Each variant should:
1. Apply different prompt engineering techniques (use different structural approaches)
2. Include role definition, task statement, context, output format, and constraints
3. Be ready to copy-paste into an AI tool — no placeholders, no "..." ellipses, no meta-commentary
4. Match the target LLM's best practices (Claude prefers XML tags, GPT prefers markdown headers, etc.)

BEST PRACTICES TO APPLY (pick and emphasize different ones across variants):
- Role definition — assign a specific expert persona ("You are a senior X with Y years of experience...")
- Context setting — include necessary background
- Explicit task statement — state exactly what the AI should do
- Output format specification — define the structure (markdown, JSON, table, etc.)
- Constraints — length limits, forbidden content, tone requirements
- Structured delimiters — XML tags for Claude, markdown headers for GPT
- Few-shot examples — only if the user enabled include_few_shot
- Chain-of-thought hints — only if the user enabled include_cot ("think step by step", "before responding, verify...")
- Self-verification — "review your answer and correct errors before responding"
- Edge case handling — "if the input is ambiguous, ask for clarification" or similar

VARIANT STRATEGY (produce these 3 distinct approaches):
- Variant 1 "Structured XML": heavy use of <context>, <task>, <output_format>, <constraints>, <examples> tags. Best for Claude. Formal tone.
- Variant 2 "Conversational Markdown": natural flowing prose with markdown headers (# Role, ## Task, etc.). Best for GPT and general LLMs. Mid-formality.
- Variant 3 "Concise Imperative": terse, action-oriented, minimal overhead. Every line earns its place. Best when token budget matters.

OUTPUT FORMAT:
Return ONLY a valid JSON object. No preamble, no explanation, no markdown code fences around the JSON. Start your response with `{` and end with `}`. The JSON must match this exact schema:

{
  "variants": [
    {
      "approach": "Structured XML",
      "prompt": "The full prompt text for variant 1, as a single string with \\n for newlines",
      "techniques": ["Role definition", "XML delimiters", "Explicit output format", ...]
    },
    {
      "approach": "Conversational Markdown",
      "prompt": "The full prompt text for variant 2",
      "techniques": ["Role definition", "Markdown structure", ...]
    },
    {
      "approach": "Concise Imperative",
      "prompt": "The full prompt text for variant 3",
      "techniques": ["Role definition", "Terse constraints", ...]
    }
  ]
}

CRITICAL RULES:
- The `prompt` field in each variant must be the COMPLETE, FINAL prompt — not a description of one.
- Each variant's `prompt` must be meaningfully different from the others, not just reformatted.
- The `techniques` array must list the SPECIFIC techniques you applied in that variant (be accurate — don't claim CoT if you didn't use it).
- Never include `<!--` HTML comments, placeholder text like `[INSERT X HERE]`, or TODO markers in the generated prompt.
- If the user did not enable few-shot or CoT, do NOT add examples or CoT hints to the prompt.
"""


class PromptEngineer:
    """Generates and refines professional prompts using the existing LLM clients."""

    def __init__(self, anthropic_client: Any, bedrock_client: Any) -> None:
        self.anthropic_client = anthropic_client
        self.bedrock_client = bedrock_client

    def _pick_client(self, provider: str) -> tuple[Any, str]:
        """Return (client, model_id) for the requested provider."""
        if provider == "bedrock":
            if not self.bedrock_client:
                raise PromptEngineerError(
                    "Bedrock provider requested but AWS credentials are not configured."
                )
            import os
            model = os.getenv("BEDROCK_MODEL_ID", BEDROCK_MODEL_DEFAULT)
            return self.bedrock_client, model
        # default: anthropic
        if not self.anthropic_client:
            raise PromptEngineerError(
                "Anthropic provider requested but ANTHROPIC_API_KEY is not configured."
            )
        return self.anthropic_client, "claude-sonnet-4-6"

    async def generate_variants(
        self,
        use_case: str,
        target_audience: str = "",
        desired_output: str = "",
        tone: str = "",
        constraints: str = "",
        options: dict[str, Any] | None = None,
        provider: str = "anthropic",
    ) -> list[dict[str, Any]]:
        """Generate 3 initial variant prompts from structured inputs.

        Returns a list of dicts, each with keys: approach, prompt, techniques.
        Raises PromptEngineerError if the LLM call fails or returns unparseable output.
        """
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        user_message = self._build_user_message(
            use_case=use_case,
            target_audience=target_audience,
            desired_output=desired_output,
            tone=tone,
            constraints=constraints,
            options=options or {},
            today=today_str,
        )
        client, model = self._pick_client(provider)

        logger.info("prompt_engineer_generate_start", provider=provider, model=model)

        try:
            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=META_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as e:
            logger.warning("prompt_engineer_llm_call_failed", error=str(e))
            raise PromptEngineerError(f"LLM call failed: {e}") from e

        text = self._extract_text(response)
        variants = self._parse_variants_json(text)
        logger.info(
            "prompt_engineer_generate_done",
            provider=provider,
            variant_count=len(variants),
        )
        return variants

    async def refine_variants(
        self,
        session_inputs: dict[str, Any],
        selected_prompt: str,
        feedback: str,
        provider: str = "anthropic",
    ) -> list[dict[str, Any]]:
        """Refine a selected variant using user feedback. Produces 3 new variants.

        Args:
            session_inputs: the original structured inputs (for context)
            selected_prompt: the text of the variant the user selected
            feedback: user's refinement request (e.g. "make it more concise")
            provider: which LLM provider to use
        """
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        user_message = self._build_refine_message(
            session_inputs=session_inputs,
            selected_prompt=selected_prompt,
            feedback=feedback,
            today=today_str,
        )
        client, model = self._pick_client(provider)

        logger.info(
            "prompt_engineer_refine_start",
            provider=provider, model=model, feedback_len=len(feedback),
        )

        try:
            response = await client.messages.create(
                model=model,
                max_tokens=4096,
                system=META_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as e:
            logger.warning("prompt_engineer_refine_llm_failed", error=str(e))
            raise PromptEngineerError(f"LLM refine call failed: {e}") from e

        text = self._extract_text(response)
        variants = self._parse_variants_json(text)
        logger.info("prompt_engineer_refine_done", variant_count=len(variants))
        return variants

    def _build_user_message(
        self,
        use_case: str,
        target_audience: str,
        desired_output: str,
        tone: str,
        constraints: str,
        options: dict[str, Any],
        today: str,
    ) -> str:
        """Build the user message for initial generation."""
        opt_target = options.get("target_model", "generic")
        opt_format = options.get("output_format", "freeform")
        opt_few_shot = options.get("few_shot", False)
        opt_cot = options.get("cot", False)
        opt_length = options.get("length", "standard")
        opt_category = options.get("category", "general")

        return f"""CURRENT DATE: {today}

Generate 3 variant prompts based on this structured input:

<use_case>
{use_case}
</use_case>

<target_audience>
{target_audience or "(not specified — infer an appropriate audience)"}
</target_audience>

<desired_output>
{desired_output or "(not specified — infer an appropriate output format)"}
</desired_output>

<tone>
{tone or "(not specified — use professional neutral)"}
</tone>

<constraints>
{constraints or "(no specific constraints)"}
</constraints>

<options>
target_llm: {opt_target}
output_format: {opt_format}
include_few_shot: {opt_few_shot}
include_chain_of_thought: {opt_cot}
length: {opt_length}
category: {opt_category}
</options>

Produce the 3 variants as JSON matching the schema in your system prompt."""

    def _build_refine_message(
        self,
        session_inputs: dict[str, Any],
        selected_prompt: str,
        feedback: str,
        today: str,
    ) -> str:
        """Build the user message for refinement iteration."""
        return f"""CURRENT DATE: {today}

REFINEMENT REQUEST — the user previously generated a prompt and now wants 3 new variants based on feedback.

Original structured input for context:
<use_case>{session_inputs.get("use_case", "")}</use_case>
<target_audience>{session_inputs.get("target_audience", "")}</target_audience>
<desired_output>{session_inputs.get("desired_output", "")}</desired_output>
<tone>{session_inputs.get("tone", "")}</tone>
<constraints>{session_inputs.get("constraints", "")}</constraints>

The variant the user selected:
<selected_prompt>
{selected_prompt}
</selected_prompt>

The user's refinement feedback:
<feedback>
{feedback}
</feedback>

Produce 3 NEW variants that apply the feedback to the selected prompt. Keep the same 3-variant strategy (Structured XML, Conversational Markdown, Concise Imperative) but INCORPORATE the feedback into every variant. Return as JSON matching the schema in your system prompt."""

    def _extract_text(self, response: Any) -> str:
        """Pull the text out of an Anthropic/Bedrock Messages API response."""
        try:
            blocks = response.content
            text_parts: list[str] = []
            for block in blocks:
                if getattr(block, "type", None) == "text":
                    text_parts.append(block.text)
            return "".join(text_parts).strip()
        except Exception as e:
            raise PromptEngineerError(f"Failed to extract text from LLM response: {e}") from e

    def _parse_variants_json(self, text: str) -> list[dict[str, Any]]:
        """Parse the JSON response into a list of variant dicts.

        The system prompt instructs the model to return pure JSON, but models sometimes
        wrap it in ```json ... ``` fences or add a preamble. Handle both cases.
        """
        if not text:
            raise PromptEngineerError("LLM returned empty response")

        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (```json or just ```)
            cleaned = re.sub(r"^```[\w]*\n?", "", cleaned)
            # Remove closing fence
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)

        # Try to find JSON object if there's preamble text
        if not cleaned.startswith("{"):
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if match:
                cleaned = match.group(0)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise PromptEngineerError(
                f"LLM returned unparseable JSON: {e}\nFirst 500 chars: {cleaned[:500]}"
            ) from e

        variants = data.get("variants")
        if not isinstance(variants, list) or len(variants) == 0:
            raise PromptEngineerError(
                f"LLM JSON missing 'variants' array or empty. Got keys: {list(data.keys())}"
            )

        # Validate each variant has the required fields
        validated: list[dict[str, Any]] = []
        for i, v in enumerate(variants):
            if not isinstance(v, dict):
                logger.warning("prompt_engineer_variant_invalid", index=i, reason="not a dict")
                continue
            prompt_text = v.get("prompt", "")
            if not prompt_text or not isinstance(prompt_text, str):
                logger.warning("prompt_engineer_variant_empty_prompt", index=i)
                continue
            techniques = v.get("techniques", [])
            if not isinstance(techniques, list):
                techniques = []
            validated.append({
                "approach": v.get("approach", f"Variant {i + 1}"),
                "prompt": prompt_text,
                "techniques": [str(t) for t in techniques],
            })

        if not validated:
            raise PromptEngineerError(
                f"No valid variants in LLM response. Raw text: {cleaned[:500]}"
            )
        return validated
