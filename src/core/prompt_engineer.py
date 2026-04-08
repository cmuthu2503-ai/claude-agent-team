"""Prompt Engineer — transforms structured user requirements into production-grade AI prompts.

This module is used by the Prompt Studio feature. It wraps the existing Anthropic,
Bedrock, and OpenAI clients (from AgentSystemExecutor) and sends a carefully-crafted
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
import os
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
4. Work well across modern LLMs (Claude, GPT, Gemini, open-source models) — markdown-based structure is universally understood

BEST PRACTICES TO APPLY (pick and emphasize different ones across variants):
- Role definition — assign a specific expert persona ("You are a senior X with Y years of experience...")
- Context setting — include necessary background
- Explicit task statement — state exactly what the AI should do
- Output format specification — define the structure (markdown, JSON, table, etc.)
- Constraints — length limits, forbidden content, tone requirements
- Structured delimiters — markdown headers (# Role, ## Task, ## Output Format)
- Few-shot examples — short, representative input/output pairs the model can pattern-match on
- Chain-of-thought hints — only if the user enabled include_cot ("think step by step", "before responding, verify...")
- Self-verification — "review your answer and correct errors before responding"
- Edge case handling — "if the input is ambiguous, ask for clarification" or similar

VARIANT STRATEGY (produce these 3 DISTINCT approaches):

1. "Detailed Markdown" — Comprehensive, thorough, safe default.
   - Rich markdown structure with multiple sections: `# Role`, `## Context`, `## Task`, `## Output Format`, `## Constraints`, `## Examples` (optional), `## Edge Cases`.
   - Verbose but unambiguous — spells out the role, scope, format requirements, and edge cases.
   - Best when: the task is complex, the output format matters, or the user wants maximum reliability.
   - Tone: professional, explicit, measured.

2. "Concise Imperative" — Terse, action-oriented, minimal.
   - Every line earns its place. No filler, no elaborate headers.
   - Short role line, explicit task, minimal format hint, 1-3 bullet constraints.
   - Best when: token budget matters, the task is simple, or the user needs a fast lightweight prompt.
   - Tone: direct, commanding, stripped-down.

3. "Example-Driven (Few-Shot)" — Pattern-based, learning-by-example.
   - Lead with role + one-sentence task, then 2-3 concrete `Input → Output` examples that demonstrate the exact desired behavior.
   - Minimal prose instructions — the examples carry most of the signal.
   - Best when: the task is pattern-matching, classification, transformation, or when describing the output in words is harder than showing it.
   - Tone: show-don't-tell, concrete.
   - IMPORTANT: the example inputs and outputs must be PLAUSIBLE, SPECIFIC to the user's use case, and FULLY written out — no placeholders like "[input example]".

OUTPUT FORMAT:
Return ONLY a valid JSON object. No preamble, no explanation, no markdown code fences around the JSON. Start your response with `{` and end with `}`. The JSON must match this exact schema:

{
  "variants": [
    {
      "approach": "Detailed Markdown",
      "prompt": "The full prompt text for variant 1, as a single string with \\n for newlines",
      "techniques": ["Role definition", "Markdown structure", "Explicit output format", "Constraint enumeration", ...]
    },
    {
      "approach": "Concise Imperative",
      "prompt": "The full prompt text for variant 2",
      "techniques": ["Role definition", "Imperative voice", "Minimal overhead", ...]
    },
    {
      "approach": "Example-Driven (Few-Shot)",
      "prompt": "The full prompt text for variant 3 including 2-3 concrete Input/Output examples",
      "techniques": ["Role definition", "Few-shot examples", "Pattern demonstration", ...]
    }
  ]
}

CRITICAL RULES:
- The `prompt` field in each variant must be the COMPLETE, FINAL prompt — not a description of one.
- Each variant's `prompt` must be meaningfully DIFFERENT from the others — not just reformatted or reworded. Different approach, different length profile, different center of gravity.
- The `techniques` array must list the SPECIFIC techniques you applied in that variant (be accurate — don't claim CoT if you didn't use it).
- Never include `<!--` HTML comments, placeholder text like `[INSERT X HERE]`, or TODO markers in the generated prompt.
- For variant 3 (Example-Driven), the examples MUST be fully written out with concrete, plausible content relevant to the user's use case — do not leave example slots empty.
- Do NOT use XML tags (<context>, <task>, etc.) in any variant — use markdown headers instead.
- If the user did not enable chain-of-thought, do NOT add CoT hints.
- If the user explicitly enabled include_few_shot, variants 1 and 2 may also include a small examples section (not just variant 3).
"""


class PromptEngineer:
    """Generates and refines professional prompts using the existing LLM clients."""

    def __init__(
        self,
        anthropic_client: Any,
        bedrock_client: Any,
        openai_client: Any = None,
    ) -> None:
        self.anthropic_client = anthropic_client
        self.bedrock_client = bedrock_client
        self.openai_client = openai_client

    def _pick_client(self, provider: str) -> tuple[Any, str, str]:
        """Return (client, model_id, kind) for the requested provider.

        `kind` is one of: "anthropic", "openai" — used by the caller to pick
        the right SDK method and response-parsing path.
        """
        if provider == "bedrock":
            if not self.bedrock_client:
                raise PromptEngineerError(
                    "Bedrock provider requested but AWS credentials are not configured."
                )
            model = os.getenv("BEDROCK_MODEL_ID", BEDROCK_MODEL_DEFAULT)
            return self.bedrock_client, model, "anthropic"

        if provider == "anthropic_opus":
            if not self.anthropic_client:
                raise PromptEngineerError(
                    "Claude Opus provider requested but ANTHROPIC_API_KEY is not configured."
                )
            return self.anthropic_client, os.getenv("ANTHROPIC_OPUS_MODEL_ID", "claude-opus-4-6"), "anthropic"

        if provider in ("anthropic", "anthropic_sonnet"):
            if not self.anthropic_client:
                raise PromptEngineerError(
                    "Claude Sonnet provider requested but ANTHROPIC_API_KEY is not configured."
                )
            return self.anthropic_client, os.getenv("ANTHROPIC_SONNET_MODEL_ID", "claude-sonnet-4-6"), "anthropic"

        if provider == "openai_gpt5":
            if not self.openai_client:
                raise PromptEngineerError(
                    "OpenAI provider requested but OPENAI_API_KEY is not configured."
                )
            return self.openai_client, os.getenv("OPENAI_GPT5_MODEL_ID", "gpt-5.4"), "openai"

        if provider == "openai_o3":
            if not self.openai_client:
                raise PromptEngineerError(
                    "OpenAI provider requested but OPENAI_API_KEY is not configured."
                )
            return self.openai_client, os.getenv("OPENAI_O3_MODEL_ID", "o4-mini"), "openai"

        raise PromptEngineerError(f"Unknown provider '{provider}'")

    async def _call_meta_prompt(
        self, provider: str, user_message: str
    ) -> str:
        """Send the meta-prompt to the chosen provider and return raw text output.

        Handles both Anthropic (Messages API with `system=`) and OpenAI (Chat
        Completions with system as the first message).
        """
        client, model, kind = self._pick_client(provider)

        if kind == "openai":
            # OpenAI Chat Completions
            messages = [
                {"role": "system", "content": META_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ]
            kwargs: dict[str, Any] = {"model": model, "messages": messages}
            is_reasoning = model.startswith("o") and not model.startswith("openai")
            if is_reasoning:
                kwargs["max_completion_tokens"] = 4096
            else:
                kwargs["max_tokens"] = 4096
            response = await client.chat.completions.create(**kwargs)
            text = response.choices[0].message.content or ""
            return text.strip()

        # Anthropic / Bedrock — Messages API
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=META_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return self._extract_text(response)

    async def generate_variants(
        self,
        use_case: str,
        target_audience: str = "",
        desired_output: str = "",
        tone: str = "",
        constraints: str = "",
        options: dict[str, Any] | None = None,
        provider: str = "anthropic_sonnet",
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

        logger.info("prompt_engineer_generate_start", provider=provider)

        try:
            text = await self._call_meta_prompt(provider, user_message)
        except PromptEngineerError:
            raise
        except Exception as e:
            logger.warning("prompt_engineer_llm_call_failed", error=str(e))
            raise PromptEngineerError(f"LLM call failed: {e}") from e

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
        provider: str = "anthropic_sonnet",
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

        logger.info(
            "prompt_engineer_refine_start",
            provider=provider, feedback_len=len(feedback),
        )

        try:
            text = await self._call_meta_prompt(provider, user_message)
        except PromptEngineerError:
            raise
        except Exception as e:
            logger.warning("prompt_engineer_refine_llm_failed", error=str(e))
            raise PromptEngineerError(f"LLM refine call failed: {e}") from e

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
