"""
vocascade/pipeline/router.py — Router pipeline stage.
"""

import time
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
from vocascade.session.teardown import contains_sentinel, strip_sentinel

logger = logging.getLogger("vocascade.pipeline.router_stage")

class RouterStage(PipelineStage):
    """
    Router pipeline stage.
    Receives TranscriptionFrame, runs the WaterfallRouter to select the winning skill,
    constructs the SkillContext, executes the skill, and pushes the text response
    downstream as TextFrame(s).
    """
    def __init__(self, router, session_state: SessionState, config, task_broker=None,
                 latency=None, delivery=None):
        super().__init__()
        self.router = router
        self.session_state = session_state
        self.config = config
        # App-level Hermes broker (US3); handed to the hermes skill via ctx.
        self.task_broker = task_broker
        # Latency masking layer (US4); emits a filler/opening before a result.
        self.latency = latency
        # Delivery coordinator (US6); backs ctx.notify for skill proactive speech.
        self.delivery = delivery
        self._out_sample_rate = getattr(config, "audio_out_sample_rate", 32000)

    async def _notify(self, text: str):
        """ctx.notify — speak `text` proactively at the next idle moment (US6)."""
        if self.delivery is not None and text:
            from vocascade.delivery import ProactiveResult, DeliveryKind
            self.delivery.enqueue(ProactiveResult(
                task_id=f"notify_{int(time.time() * 1000)}",
                kind=DeliveryKind.RESULT, preamble="",
                speech_text=text, full_text=text))

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

    async def _speak_result(self, called, utterance: str, context):
        """Push a handler/resume result (str, coroutine, or async generator) to TTS.
        Streamed (async-gen) results get progressive fillers; only real content
        feeds the assistant_response message."""
        try:
            if inspect.isasyncgen(called):
                spoken: list[str] = []
                if self.latency is not None:
                    async for chunk, is_filler in self.latency.with_progressive_fillers(
                        called, utterance, context.local_llm
                    ):
                        if chunk:
                            await super().push(TextFrame(text=chunk))
                            if not is_filler:
                                spoken.append(chunk)
                else:
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
                    # A model-emitted ENDSESSION arms teardown; strip it so it is
                    # never spoken or shown (US5 / FR-062).
                    if contains_sentinel(res):
                        if context.session is not None:
                            context.session.teardown_armed = True
                        res = strip_sentinel(res).strip()
                    if res:
                        await super().push(ControlMessageFrame({"type": "assistant_response", "text": res}))
                        await super().push(TextFrame(text=res))
                elif hasattr(res, "__aiter__"):
                    async for chunk in res:
                        if chunk:
                            await super().push(TextFrame(text=chunk))
        except Exception as e:
            logger.error(f"Error speaking result: {e}", exc_info=True)

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
                notify=self._notify,
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

            # 2b. CONVERSE — a skill claimed this turn; route it to the claim's resume.
            if result.payload.get("converse") and context.session.converse_claim is not None:
                claim = context.session.converse_claim
                context.session.converse_claim = None        # released on consumption
                await self._speak_result(claim.resume(frame.text, context), frame.text, context)
                return

            if not result.skill_name:
                logger.warning("No skill resolved for this transcription.")
                return

            # 2c. Latency masking (US4): play a filler/opening before the result.
            if self.latency is not None:
                try:
                    await self.latency.mask(
                        stage=result.stage,
                        skill_config=config_dict.get(result.skill_name, {}),
                        utterance=frame.text,
                        local_llm=context.local_llm,
                        emit_text=self._emit_text,
                    )
                except Exception as e:
                    logger.error(f"Latency masking failed: {e}", exc_info=True)

            # 3. Execute the winning skill.
            skill_obj = registry.get_skill(result.skill_name)
            if skill_obj is None:
                logger.error(f"Routed to skill '{result.skill_name}' but it is not registered.")
                return
            # Stage payload (e.g. STOP's action, HIGH's matched keyword) flows to
            # the handler as `entities`.
            await self._speak_result(
                skill_obj.handler(frame.text, result.payload, context), frame.text, context)

        else:
            # Pass all other frames downstream
            await super().push(frame)
