"""
vocascade/pipeline/router.py — Router pipeline stage.
"""

import logging
from vocascade.pipeline.pipeline import (
    PipelineStage,
    Frame,
    TranscriptionFrame,
    TextFrame,
    ControlMessageFrame,
)
from vocascade.skills.context import SkillContext, ToolBag
from vocascade.skills.registry import registry
from vocascade.session.state import SessionState

logger = logging.getLogger("vocascade.pipeline.router_stage")

class RouterStage(PipelineStage):
    """
    Router pipeline stage.
    Receives TranscriptionFrame, runs the WaterfallRouter to select the winning skill,
    constructs the SkillContext, executes the skill, and pushes the text response
    downstream as TextFrame(s).
    """
    def __init__(self, router, session_state: SessionState, config):
        super().__init__()
        self.router = router
        self.session_state = session_state
        self.config = config

    async def push(self, frame: Frame):
        if isinstance(frame, TranscriptionFrame):
            logger.info(f"RouterStage processing transcription: '{frame.text}'")

            # Surface the recognised utterance to the client UI/log.
            await super().push(ControlMessageFrame({"type": "transcript", "text": frame.text}))

            # 1. Create a SkillContext with combined config settings
            config_dict = dict(self.config.skills_config) if self.config.skills_config else {}
            config_dict["tts_character_name"] = self.config.tts_character_name

            context = SkillContext(
                tools=ToolBag(),
                session=self.session_state,
                history=[],
                config=config_dict,
                emit_filler=None,
                local_llm=None
            )
            
            # Resolve local_llm client if base_url is configured
            from vocascade.gateway.local_llm import LocalLLM
            if self.config.llm_base_url:
                context.local_llm = LocalLLM(
                    base_url=self.config.llm_base_url,
                    api_key=self.config.llm_api_key,
                    model=self.config.llm_model
                )
            
            # 2. Resolve via WaterfallRouter
            result = await self.router.resolve(frame.text, context)
            
            # 3. Execute the winning skill
            if result.skill_name:
                skill_obj = registry.get_skill(result.skill_name)
                if skill_obj:
                    try:
                        # Executing the handler
                        res = await skill_obj.handler(frame.text, {}, context)
                        
                        # Handle return: can be a string or async generator
                        if isinstance(res, str):
                            # Surface the full reply text (also the only signal in
                            # degraded TTS mode, where no audio is synthesised).
                            await super().push(ControlMessageFrame({"type": "assistant_response", "text": res}))
                            await super().push(TextFrame(text=res))
                        elif hasattr(res, "__aiter__"):
                            async for chunk in res:
                                if chunk:
                                    await super().push(TextFrame(text=chunk))
                    except Exception as e:
                        logger.error(f"Error executing skill '{result.skill_name}': {e}", exc_info=True)
                else:
                    logger.error(f"Routed to skill '{result.skill_name}' but it is not registered.")
            else:
                logger.warning("No skill resolved for this transcription.")
                
        else:
            # Pass all other frames downstream
            await super().push(frame)
