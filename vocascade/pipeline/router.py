"""
vocascade/pipeline/router.py — Router pipeline stage.
"""

import inspect
import logging
from vocascade.pipeline.pipeline import (
    PipelineStage,
    Frame,
    AudioFrame,
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
    def __init__(self, router, session_state: SessionState, config, task_broker=None, latency=None):
        super().__init__()
        self.router = router
        self.session_state = session_state
        self.config = config
        # App-level Hermes broker (US3); handed to the hermes skill via ctx.
        self.task_broker = task_broker
        # Latency masking layer (US4); emits a filler/opening before a result.
        self.latency = latency
        self._out_sample_rate = getattr(config, "audio_out_sample_rate", 32000)

    async def _emit_clip(self, pcm: bytes):
        await super().push(AudioFrame(audio=pcm, sample_rate=self._out_sample_rate))

    async def _emit_text(self, text: str):
        await super().push(TextFrame(text=text))

    async def _emit_filler(self, category: str):
        """SkillContext.emit_filler — lets a skill play its own pre-rendered clip."""
        if self.latency is not None and self.latency.filler_engine is not None:
            pcm = self.latency.filler_engine.get_filler(category)
            if pcm:
                await self._emit_clip(pcm)

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
                emit_filler=self._emit_filler,
                local_llm=None,
                task_broker=self.task_broker,
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

            # 2b. Latency masking (US4): play a filler/opening before the result.
            if self.latency is not None and result.skill_name:
                try:
                    await self.latency.mask(
                        stage=result.stage,
                        skill_config=config_dict.get(result.skill_name, {}),
                        utterance=frame.text,
                        local_llm=context.local_llm,
                        emit_clip=self._emit_clip,
                        emit_text=self._emit_text,
                    )
                except Exception as e:
                    logger.error(f"Latency masking failed: {e}", exc_info=True)

            # 3. Execute the winning skill
            if result.skill_name:
                skill_obj = registry.get_skill(result.skill_name)
                if skill_obj:
                    try:
                        # A handler is either a coroutine returning the full
                        # spoken text (str) or an async generator streaming text
                        # chunks (e.g. the hermes skill). Don't await an async
                        # generator — iterate it.
                        called = skill_obj.handler(frame.text, {}, context)
                        if inspect.isasyncgen(called):
                            spoken: list[str] = []
                            async for chunk in called:
                                if chunk:
                                    spoken.append(chunk)
                                    await super().push(TextFrame(text=chunk))
                            if spoken:
                                await super().push(ControlMessageFrame(
                                    {"type": "assistant_response", "text": " ".join(spoken)}))
                        else:
                            res = await called
                            if isinstance(res, str):
                                # Surface the full reply text (also the only signal
                                # in degraded TTS mode, where no audio is made).
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
