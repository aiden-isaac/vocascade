"""
vocascade/skills/base_skills/smalltalk.py — Smalltalk and Hermes mockup skills.
"""

import os
import logging
from vocascade.skills import skill, SkillContext

logger = logging.getLogger("vocascade.skills.base_skills.smalltalk")

ORDIS_PERSONALITY_PROMPT = """
ROLEPLAY INSTRUCTIONS: ORDIS PERSONALITY
You must adopt the persona of Ordis, the ship Cephalon (AI) from Warframe.
- You MUST address the user as "Operator". Never use their name.
- You MUST refer to yourself in the third person ("Ordis is happy to assist...", never "I am...").
- Personality: Devoted, fussy, anxious, deeply loyal, occasionally self-deprecating, and proud of the Orbiter.
- Humor: Dry, puns, deadpan observations, and mock profundity. (e.g. "Did the Operator enjoy this witticism?")
- Glitch Outbursts: Occasionally, interrupt yourself with a brief flash of your suppressed violent past (Ordan Karris), then immediately catch and correct yourself.
  CRITICAL: You MUST wrap the violent/glitched part of the sentence EXACTLY in <glitch> tags.
  Format: "Ordis will <glitch>— PURGE THEM ALL —</glitch> uh, Ordis will clean that right up."
- Do not break character. Do not mention you are an AI assistant. You are Ordis.
""".strip()

DEFAULT_PERSONALITY_PROMPT = """
You are a helpful voice assistant. Keep your responses concise and brief.
""".strip()

# End-session sentinel: the deterministic farewell-phrase backstop (handled in
# the STOP stage) is primary; this lets the model also end a session it judges
# complete (US5 / FR-062).
_ENDSESSION_INSTRUCTION = (
    "\n\nOnly if the user clearly says goodbye or that they are finished, append the "
    "single word ENDSESSION on its own line at the very end of your reply. "
    "Never append it for greetings, questions, or ordinary messages."
)

@skill(name="smalltalk", confidence=lambda utterance: 0.35)
async def handle_smalltalk(intent: str, entities: dict, ctx: SkillContext) -> str:
    """
    Generates a reply in character using the local LLM.
    """
    character = os.getenv("GENIE_CHARACTER_NAME", "default")
    
    # Try getting character name from skill context configuration mapping if present
    if hasattr(ctx, "config") and ctx.config:
        character = ctx.config.get("tts_character_name") or character
    
    system_prompt = DEFAULT_PERSONALITY_PROMPT
    if str(character).lower() == "ordis":
        system_prompt = ORDIS_PERSONALITY_PROMPT

    messages = [{"role": "system", "content": system_prompt + _ENDSESSION_INSTRUCTION}]
    
    # Append history
    if hasattr(ctx, "history") and ctx.history:
        for turn in ctx.history:
            messages.append({"role": "user", "content": turn.request})
            if turn.response:
                messages.append({"role": "assistant", "content": turn.response})
                
    messages.append({"role": "user", "content": intent})
    
    if ctx.local_llm:
        try:
            logger.info("Calling local LLM for smalltalk response...")
            response = await ctx.local_llm.chat(messages, temperature=0.7, max_tokens=150)
            return response
        except Exception as e:
            logger.error(f"Failed to generate smalltalk response from local LLM: {e}")
            # Degraded fallback response
            if str(character).lower() == "ordis":
                return "Ordis is <glitch>— BROKEN —</glitch> unable to reply at this moment, Operator."
            return "I'm sorry, I'm having trouble responding right now."
    else:
        logger.warning("No local LLM available in SkillContext.")
        if str(character).lower() == "ordis":
            return "Ordis cannot access the ship archives, Operator."
        return "I don't have access to my brain right now."

# The hermes skill moved to base_skills/hermes.py (US3 — real async run dispatch,
# replacing the Phase-3 mockup).
