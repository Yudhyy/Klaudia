import logging
import re
import time
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, AsyncIterator

from app.models.attachment import FileAttachment
from app.models.chat import (
    ChatMetadata,
    KlaudiaMessage,
    KlaudiaResponse,
)
from app.services.core.container import KlaudiaContainer
from app.services.core.main_chat import MainChatTurn
from klaudia.core.agent.agent import RunOutcome
from app.services.core.prompts import KLAUDIA_SYSTEM_PROMPT
from app.services.core.verifier import verify_reply
from app.services.extraction.infra.normalizer import is_pdf, is_supported_image
from klaudia.core.supervisor.tools.context import (
    ExtractionContextFormat,
    build_continuity_context,
    build_extraction_context,
    build_session_context,
    get_active_spreadsheet,
    reset_active_spreadsheet,
    reset_approval_gate,
    reset_tool_trace,
    set_active_spreadsheet,
    set_approval_gate,
    start_tool_trace,
)

# Bounds for the evidence block fed to the numeric-correction rewrite.
_EVIDENCE_RECORD_CHARS = 2000
_EVIDENCE_TOTAL_CHARS = 8000

logger = logging.getLogger(__name__)


@contextmanager
def _nullctx():
    yield None


def _classify_attachments(
    attachments: list[FileAttachment],
) -> tuple[int, int, list[FileAttachment]]:
    """Return (image_count, pdf_count, unknown_attachments).

    Unknown = neither image nor pdf; rejected pre-OCR. Used by the orchestrator
    to enforce the 'max 5 images' rule before any GPU work.
    """
    images = 0
    pdfs = 0
    unknown: list[FileAttachment] = []
    for att in attachments:
        if is_pdf(att.filename, att.content_type):
            pdfs += 1
        elif is_supported_image(att.filename, att.content_type):
            images += 1
        else:
            unknown.append(att)
    return images, pdfs, unknown


def _check_attachment_shape(
    attachments: list[FileAttachment], max_images: int
) -> str | None:
    """Return rejection message if shape violates upload rules, else None.

    Rules:
        - <= 1 PDF
        - PDF and images cannot mix in the same turn
        - <= max_images standalone images
        - All attachments must be a supported image or pdf
    """
    images, pdfs, unknown = _classify_attachments(attachments)
    if pdfs > 1:
        return "Hanya satu PDF per pesan ya. Coba kirim ulang dengan 1 PDF saja."
    if pdfs == 1 and images > 0:
        return "Mau PDF atau gambar — tidak campur ya. Pilih salah satu."
    if images > max_images:
        return f"Maksimal {max_images} gambar per pesan. Ada {images} gambar terlampir."
    if unknown:
        names = ", ".join(a.filename for a in unknown)
        return f"Format tidak didukung: {names}. Upload JPG/PNG/HEIC/PDF saja."
    return None


def _format_evidence(tool_trace: list[tuple[str, dict, str]]) -> str:
    """Bounded raw tool outputs for the numeric-correction rewrite."""
    parts: list[str] = []
    total = 0
    for name, _args, output in tool_trace:
        snippet = (output or "")[:_EVIDENCE_RECORD_CHARS]
        total += len(snippet)
        if total > _EVIDENCE_TOTAL_CHARS:
            break
        parts.append(f"[{name}]\n{snippet}")
    return "\n\n".join(parts)


def _build_extraction_contexts(
    extraction_results: list[dict[str, Any]],
    output_format: ExtractionContextFormat,
) -> list[str]:
    """Serialize each attachment once for prompt, history, and verification."""
    return [
        build_extraction_context(extraction_result, output_format)
        for extraction_result in extraction_results
    ]


class KlaudiaOrchestrator:
    """Main conversation orchestrator.

    Pipeline: context -> guardrails -> route (extraction/supervisor) -> response
    """

    def __init__(self, container: KlaudiaContainer) -> None:
        self._c = container
        self._extraction_agent = container.extraction_agent
        self._langfuse = container.langfuse
        # Strong refs to in-flight background memory writes so they are not
        # garbage-collected before completing.
        self._bg_tasks: set[Any] = set()

    async def process(
        self,
        messages: list[KlaudiaMessage],
        session_id: int | None,
        user_id: int,
        user_name: str = "User",
        spreadsheet_id: str | None = None,
        request_key: str | None = None,
    ) -> KlaudiaResponse:
        start = time.time()

        # 0. Bind the tenant scope for this request. Everything downstream
        # (sheets cache, agent tool calls) reads it from the ContextVar.
        # Raises SpreadsheetNotFoundError for absent/foreign ids (route -> 404).
        scope = await self._resolve_scope(user_id, spreadsheet_id)
        scope_token = set_active_spreadsheet(scope)
        try:
            return await self._process_scoped(
                messages, session_id, user_id, user_name, start, request_key=request_key
            )
        finally:
            reset_active_spreadsheet(scope_token)

    async def _process_scoped(
        self,
        messages: list[KlaudiaMessage],
        session_id: int | None,
        user_id: int,
        user_name: str,
        start: float,
        *,
        request_key: str | None = None,
    ) -> KlaudiaResponse:
        # 1. Ensure session exists
        if session_id is None:
            session_id = await self._c.db_client.create_session(user_id)

        langfuse = self._langfuse
        trace_cm = (
            langfuse.trace_attributes(
                session_id=session_id, user_id=user_id, tags=["klaudia", "chat"]
            )
            if langfuse is not None
            else _nullctx()
        )
        span_cm = (
            langfuse.span(
                "klaudia.process",
                as_type="agent",
                input={"user_text": messages[-1].content, "user_name": user_name},
                metadata={"session_id": session_id, "user_id": user_id},
            )
            if langfuse is not None
            else _nullctx()
        )

        with trace_cm, span_cm as turn_obs:
            response = await self._process_inner(
                messages, session_id, user_id, user_name, start, request_key=request_key
            )
            if turn_obs is not None:
                try:
                    turn_obs.update(
                        output={
                            "content": response.message.content,
                            "tools_used": response.tools_used,
                            "processing_time_ms": response.processing_time_ms,
                        }
                    )
                except Exception:
                    pass

            return response

    async def _process_inner(
        self,
        messages: list[KlaudiaMessage],
        session_id: int,
        user_id: int,
        user_name: str,
        start: float,
        *,
        request_key: str | None = None,
    ) -> KlaudiaResponse:

        # 2. Get last user message text
        user_msg = messages[-1]
        user_text = user_msg.content

        # 3. Guardrails (input)
        guard_result = await self._c.guardrails.validate_input(user_text)
        if not guard_result.passed:
            return self._rejection_response(
                session_id, guard_result.rejection_message, start
            )

        # 4. Check for attachments. Accumulate per-attachment results so multi-
        # image uploads (1..N images, where N <= MAX_IMAGES_PER_UPLOAD) are
        # surfaced to the supervisor in upload order.
        extraction_results: list[dict[str, Any]] = []
        has_attachment = user_msg.attachments and len(user_msg.attachments) > 0
        if has_attachment:
            attachments = list(user_msg.attachments)
            max_images = self._c.settings.max_images_per_upload
            rejection = _check_attachment_shape(attachments, max_images)
            if rejection is not None:
                return self._rejection_response(session_id, rejection, start)

            # PRV: queue depth gate (only meaningful in async mode but cheap to check)
            prv_msg = await self._check_queue_pressure()
            if prv_msg is not None:
                return self._rejection_response(session_id, prv_msg, start)

            for att in attachments:
                # Note: process() (sync mode) and the async path both end up
                # writing to the same DB tables. We always go through
                # ExtractionAgent here because process() is non-streaming and
                # callers expect data ready on return.
                result = await self._extraction_agent.process(att, session_id, user_id)
                extraction_results.append(
                    {
                        "file_id": result.file_id,
                        "file_name": result.file_name,
                        "pages": result.pages,
                        "status": result.status,
                        "summary": result.summary,
                    }
                )

        if self._c.settings.chat_runtime == "main":
            return await self._process_main_chat(
                MainChatTurn(
                    user_id=user_id,
                    session_id=session_id,
                    active_workbook_id=get_active_spreadsheet(),
                    text=user_text,
                    request_key=request_key,
                    extraction_contexts=(),
                    metadata=ChatMetadata(user_name=user_name),
                ),
                start,
                extractions=extraction_results,
            )

        # 5. Build context
        # NOTE: history is fetched BEFORE saving the current user msg so the
        # current msg is appended exactly once at the tail of llm_messages.
        # Saving first would double the user turn (history + explicit append),
        # which confuses the LLM (two identical consecutive user messages).
        import asyncio

        (
            history,
            session_files_raw,
            available_sheets,
            recent_activity,
            memory_ctx,
        ) = await asyncio.gather(
            self._c.db_client.get_conversation_history(session_id, limit=10),
            self._c.db_client.get_session_files(session_id),
            self._c.supervisor.get_available_sheets(),
            self._recent_activity_context(),
            self._recall_memory(user_id, user_text),
        )

        meta = ChatMetadata(user_name=user_name)
        session_files_ctx = build_session_context(session_files_raw)
        system_prompt = KLAUDIA_SYSTEM_PROMPT.format(
            session_files=session_files_ctx,
            available_sheets=available_sheets or "No sheets available.",
            recent_activity=recent_activity or "No recent sheet activity on record.",
            memory_context=memory_ctx or "Nothing remembered about this user yet.",
            session_id=session_id,
            date=meta.date,
            time=meta.time,
            timezone=meta.timezone,
        )
        extraction_contexts = _build_extraction_contexts(
            extraction_results,
            self._c.settings.extraction_context_format,
        )

        # Build messages for supervisor
        llm_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

        # Add history (reversed to chronological)
        for row in reversed(history):
            llm_messages.append(
                {
                    "role": row["sender"],
                    "content": row["message_text"],
                }
            )

        # Add extraction context if present (one block per attachment, in order)
        for extraction_ctx in extraction_contexts:
            llm_messages.append({"role": "user", "content": extraction_ctx})

        # Add current user message
        llm_messages.append({"role": "user", "content": user_text})

        # 6. Persist extraction contexts (if any) then the user message to DB.
        # Extraction contexts MUST be saved so future turns can access the full
        # item-level data when the user follows up (e.g., "masukkan ke sheet"
        # after "ini total berapa?"). Without this, write_agent has no data.
        for extraction_ctx in extraction_contexts:
            if extraction_ctx:
                await self._c.db_client.save_message(
                    session_id, user_id, "user", extraction_ctx
                )
        await self._c.db_client.save_message(session_id, user_id, "user", user_text)

        # 7. Invoke supervisor. Pass last extraction so legacy callers that
        # expect a single extraction_data still see something; the full list
        # is already encoded in llm_messages above.
        last_extraction = extraction_results[-1] if extraction_results else None
        tool_trace, trace_token = start_tool_trace()
        gate_cm = self._approval_gate(user_id, session_id, get_active_spreadsheet())
        try:
            with gate_cm as parked_approvals:
                agent_response = await self._c.supervisor.process_conversation(
                    messages=llm_messages,
                    extraction_data=last_extraction,
                    session_id=session_id,
                    user_id=user_id,
                    sheets_context=available_sheets or "",
                    files_context=session_files_ctx or "",
                    date_context=(
                        f"CURRENT DATE/TIME: {meta.date} {meta.time} ({meta.timezone})"
                    ),
                )
        finally:
            reset_tool_trace(trace_token)

        # 8. Post-process: remove thinking tokens
        content = re.sub(
            r"<think>.*?</think>", "", agent_response.content, flags=re.DOTALL
        ).strip()

        # 8b. Deterministic numeric verification (never trust LLM arithmetic).
        extra_texts = (
            [user_text] + extraction_contexts + [row["message_text"] for row in history]
        )
        content = await self._verify_numeric(content, tool_trace, extra_texts)

        # 9. Output guardrails
        output_guard = await self._c.guardrails.validate_output(content)
        if not output_guard.passed:
            content = output_guard.rejection_message

        # 10. Save assistant message
        await self._c.db_client.save_message(session_id, user_id, "assistant", content)
        await self._c.db_client.update_session_timestamp(session_id)

        # 11. Persist long-term memory in the background (reply already built).
        self._remember(user_id, get_active_spreadsheet(), user_text, content)

        elapsed = int((time.time() - start) * 1000)
        return KlaudiaResponse(
            message=KlaudiaMessage(role="assistant", content=content),
            session_id=session_id,
            processing_time_ms=elapsed,
            tools_used=agent_response.tools_called,
            metadata=meta,
            pending_approvals=[a.as_payload() for a in parked_approvals],
        )

    async def stream(
        self,
        messages: list[KlaudiaMessage],
        session_id: int | None,
        user_id: int,
        user_name: str = "User",
        spreadsheet_id: str | None = None,
        request_key: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream the pipeline as structured SSE-ready events.

        Event shape: {"type": <name>, "data": <payload>}
        Types: session, guardrail, extraction, step, tool, token, done, error.
        """
        start = time.time()
        scope_token = None

        try:
            # Tenant scope, same as process(). A resolution failure surfaces
            # as an error event here; the HTTP route pre-validates explicit
            # ids so clients still get a clean 404 before streaming starts.
            scope = await self._resolve_scope(user_id, spreadsheet_id)
            scope_token = set_active_spreadsheet(scope)

            if session_id is None:
                session_id = await self._c.db_client.create_session(user_id)
            yield {"type": "session", "data": {"session_id": session_id}}

            langfuse = self._langfuse
            trace_cm = (
                langfuse.trace_attributes(
                    session_id=session_id,
                    user_id=user_id,
                    tags=["klaudia", "chat", "stream"],
                )
                if langfuse is not None
                else _nullctx()
            )
            span_cm = (
                langfuse.span(
                    "klaudia.stream",
                    as_type="agent",
                    input={"user_text": messages[-1].content, "user_name": user_name},
                    metadata={"session_id": session_id, "user_id": user_id},
                )
                if langfuse is not None
                else _nullctx()
            )
            # Intentionally NOT using `with` as an async generator wrapper so events
            # keep streaming; manually enter and ensure exit in finally block.
            _entered_trace = trace_cm.__enter__()
            turn_obs = span_cm.__enter__()

            user_msg = messages[-1]
            user_text = user_msg.content

            yield {
                "type": "guardrail",
                "data": {"stage": "input", "status": "checking"},
            }
            guard_result = await self._c.guardrails.validate_input(user_text)
            if not guard_result.passed:
                rejection = guard_result.rejection_message
                yield {
                    "type": "guardrail",
                    "data": {
                        "stage": "input",
                        "status": "rejected",
                        "message": rejection,
                    },
                }
                yield {"type": "token", "data": {"text": rejection}}
                elapsed = int((time.time() - start) * 1000)
                yield {
                    "type": "done",
                    "data": {
                        "session_id": session_id,
                        "processing_time_ms": elapsed,
                        "tools_used": [],
                        "content": rejection,
                    },
                }
                return
            yield {"type": "guardrail", "data": {"stage": "input", "status": "passed"}}

            # User msg is persisted AFTER llm_messages is built (see the
            # equivalent step in process()) so history doesn't double-count it.

            extraction_results: list[dict[str, Any]] = []
            has_attachment = user_msg.attachments and len(user_msg.attachments) > 0
            if has_attachment:
                attachments = list(user_msg.attachments)
                max_images = self._c.settings.max_images_per_upload
                rejection_msg = _check_attachment_shape(attachments, max_images)
                if rejection_msg is not None:
                    yield {
                        "type": "guardrail",
                        "data": {
                            "stage": "attachment",
                            "status": "rejected",
                            "message": rejection_msg,
                        },
                    }
                    yield {"type": "token", "data": {"text": rejection_msg}}
                    elapsed = int((time.time() - start) * 1000)
                    yield {
                        "type": "done",
                        "data": {
                            "session_id": session_id,
                            "processing_time_ms": elapsed,
                            "tools_used": [],
                            "content": rejection_msg,
                        },
                    }
                    return

                prv_msg = await self._check_queue_pressure()
                if prv_msg is not None:
                    yield {
                        "type": "guardrail",
                        "data": {
                            "stage": "queue",
                            "status": "rejected",
                            "message": prv_msg,
                        },
                    }
                    yield {"type": "token", "data": {"text": prv_msg}}
                    elapsed = int((time.time() - start) * 1000)
                    yield {
                        "type": "done",
                        "data": {
                            "session_id": session_id,
                            "processing_time_ms": elapsed,
                            "tools_used": [],
                            "content": prv_msg,
                        },
                    }
                    return

                async for event in self._run_extraction_stream(
                    attachments, session_id, user_id
                ):
                    if event.get("__final__"):
                        extraction_results = event["payload"]
                        continue
                    yield event

            if self._c.settings.chat_runtime == "main":
                response = await self._process_main_chat(
                    MainChatTurn(
                        user_id=user_id,
                        session_id=session_id,
                        active_workbook_id=get_active_spreadsheet(),
                        text=user_text,
                        request_key=request_key,
                        extraction_contexts=(),
                        metadata=ChatMetadata(user_name=user_name),
                    ),
                    start,
                    extractions=extraction_results,
                )
                if turn_obs is not None:
                    try:
                        turn_obs.update(output=response.model_dump())
                    except Exception:
                        logger.warning(
                            "Could not record main chat output trace", exc_info=True
                        )
                span_cm.__exit__(None, None, None)
                trace_cm.__exit__(None, None, None)
                yield {"type": "token", "data": {"text": response.message.content}}
                for approval in response.pending_approvals:
                    yield {"type": "approval_required", "data": approval}
                yield {
                    "type": "done",
                    "data": {
                        **response.model_dump(exclude={"message"}),
                        "content": response.message.content,
                    },
                }
                return

            import asyncio

            (
                history,
                session_files_raw,
                available_sheets,
                recent_activity,
                memory_ctx,
            ) = await asyncio.gather(
                self._c.db_client.get_conversation_history(session_id, limit=10),
                self._c.db_client.get_session_files(session_id),
                self._c.supervisor.get_available_sheets(),
                self._recent_activity_context(),
                self._recall_memory(user_id, user_text),
            )

            meta = ChatMetadata(user_name=user_name)
            session_files_ctx = build_session_context(session_files_raw)
            system_prompt = KLAUDIA_SYSTEM_PROMPT.format(
                session_files=session_files_ctx,
                available_sheets=available_sheets or "No sheets available.",
                recent_activity=recent_activity
                or "No recent sheet activity on record.",
                memory_context=memory_ctx or "Nothing remembered about this user yet.",
                session_id=session_id,
                date=meta.date,
                time=meta.time,
                timezone=meta.timezone,
            )
            extraction_contexts = _build_extraction_contexts(
                extraction_results,
                self._c.settings.extraction_context_format,
            )

            llm_messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt}
            ]
            for row in reversed(history):
                llm_messages.append(
                    {"role": row["sender"], "content": row["message_text"]}
                )
            for extraction_ctx in extraction_contexts:
                llm_messages.append({"role": "user", "content": extraction_ctx})
            llm_messages.append({"role": "user", "content": user_text})

            # Persist extraction contexts then user message (mirrors _process_inner).
            for extraction_ctx in extraction_contexts:
                if extraction_ctx:
                    await self._c.db_client.save_message(
                        session_id, user_id, "user", extraction_ctx
                    )
            await self._c.db_client.save_message(session_id, user_id, "user", user_text)

            last_extraction = extraction_results[-1] if extraction_results else None
            final_content = ""
            tools_used: list[str] = []
            any_token_emitted = False  # track whether supervisor emitted token events
            tool_trace, trace_token = start_tool_trace()
            gate_cm = self._approval_gate(user_id, session_id, get_active_spreadsheet())
            parked_approvals: list[Any] = []
            try:
                with gate_cm as parked_approvals:
                    async for event in self._c.supervisor.stream_conversation(
                        messages=llm_messages,
                        extraction_data=last_extraction,
                        session_id=session_id,
                        user_id=user_id,
                        sheets_context=available_sheets or "",
                        files_context=session_files_ctx or "",
                        date_context=(
                            f"CURRENT DATE/TIME: {meta.date} {meta.time} "
                            f"({meta.timezone})"
                        ),
                    ):
                        if event["type"] == "final":
                            final_content = event["data"]["content"]
                            tools_used = event["data"]["tools_called"]
                            continue
                        if event["type"] == "token":
                            any_token_emitted = True
                        yield event
            finally:
                reset_tool_trace(trace_token)

            content = re.sub(
                r"<think>.*?</think>", "", final_content, flags=re.DOTALL
            ).strip()

            # Numeric verification. Enforcement (rewrite) is only possible on
            # the buffered inline-FINISH path; once tokens have streamed to
            # the client the reply cannot be recalled — log-only there.
            extra_texts = (
                [user_text]
                + extraction_contexts
                + [row["message_text"] for row in history]
            )
            content = await self._verify_numeric(
                content, tool_trace, extra_texts, allow_enforce=not any_token_emitted
            )

            if not any_token_emitted and content:
                # RouterWithResponse inline FINISH path: supervisor assembled the
                # complete answer in one LLM call and emitted no token events.
                # Stream the content word-by-word now, running output_guard
                # concurrently so the guard adds zero latency to token delivery.
                output_guard_task = asyncio.create_task(
                    self._c.guardrails.validate_output(content)
                )
                words = content.split(" ")
                for i, word in enumerate(words):
                    token_text = word if i == len(words) - 1 else word + " "
                    yield {"type": "token", "data": {"text": token_text}}
                output_guard = await output_guard_task
            else:
                # final_llm path: tokens already streamed by supervisor.
                # Run output_guard sequentially (guard delay is post-stream, acceptable).
                output_guard = await self._c.guardrails.validate_output(content)

            if not output_guard.passed:
                content = output_guard.rejection_message
                yield {
                    "type": "guardrail",
                    "data": {
                        "stage": "output",
                        "status": "rejected",
                        "message": content,
                    },
                }

            await self._c.db_client.save_message(
                session_id, user_id, "assistant", content
            )
            await self._c.db_client.update_session_timestamp(session_id)

            # Persist long-term memory in the background (reply already streamed).
            self._remember(user_id, get_active_spreadsheet(), user_text, content)

            elapsed = int((time.time() - start) * 1000)
            if turn_obs is not None:
                try:
                    turn_obs.update(
                        output={
                            "content": content,
                            "tools_used": tools_used,
                            "processing_time_ms": elapsed,
                        }
                    )
                except Exception:
                    pass
            approvals = [a.as_payload() for a in parked_approvals]
            for approval in approvals:
                yield {"type": "approval_required", "data": approval}
            yield {
                "type": "done",
                "data": {
                    "session_id": session_id,
                    "processing_time_ms": elapsed,
                    "tools_used": tools_used,
                    "content": content,
                    "pending_approvals": approvals,
                },
            }
            span_cm.__exit__(None, None, None)
            trace_cm.__exit__(None, None, None)

        except Exception as exc:
            logger.exception("Stream pipeline error")
            try:
                span_cm.__exit__(type(exc), exc, exc.__traceback__)
                trace_cm.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
            yield {"type": "error", "data": {"message": str(exc)}}
        finally:
            if scope_token is not None:
                reset_active_spreadsheet(scope_token)

    async def _process_main_chat(
        self, turn: MainChatTurn, start: float, *, extractions: list[dict[str, Any]]
    ) -> KlaudiaResponse:
        """Run opt-in chat and retain evidence even when prose checks reject it.

        Args:
            turn: Server-authenticated intent and extracted facts.
            start: Request start time used for response latency.
            extractions: Completed document facts from the shared extraction path.

        Returns:
            Buffered, checked reply with explicit runtime and operation evidence.

        Raises:
            RuntimeError: Main runtime was selected without its service.
            Exception: Execution or persistence failed before a recoverable outcome.
        """
        if self._c.main_chat is None:
            raise RuntimeError("Main chat service is not configured")
        turn = replace(
            turn,
            extraction_contexts=tuple(
                _build_extraction_contexts(
                    extractions, self._c.settings.extraction_context_format
                )
            ),
            document_ids=tuple(item["file_id"] for item in extractions),
            memory_context=await self._recall_memory(turn.user_id, turn.text),
        )
        outcome = await self._c.main_chat.run(turn)
        return await self._finish_main_chat(turn, outcome, start)

    async def resume_task(self, user_id: int, task_id: str) -> KlaudiaResponse:
        """Resume persisted intent without allowing a replacement task payload.

        Args:
            user_id: Authenticated task owner.
            task_id: Original task identity.

        Returns:
            Checked response with original task and operation identities.
        """
        start = time.time()
        turn, outcome = await self._c.main_chat.resume(user_id, task_id)
        return await self._finish_main_chat(turn, outcome, start)

    async def _finish_main_chat(
        self, turn: MainChatTurn, outcome: RunOutcome, start: float
    ) -> KlaudiaResponse:
        """Check prose and persist the response without changing operation evidence.

        Args:
            turn: Original authenticated chat facts.
            outcome: Observed execution result, including partial commits.
            start: Request start time.

        Returns:
            Client response with durable task and operation references.
        """
        content = re.sub(
            r"<think>.*?</think>", "", outcome.content, flags=re.DOTALL
        ).strip()
        if outcome.status == "awaiting_approval":
            content = "The proposed operation is waiting for your approval. Earlier committed steps remain recorded; the pending operation has not run."
        elif outcome.status != "answered":
            content = (
                f"The task stopped ({outcome.status}). Completion is not confirmed."
            )
            if outcome.operation_references:
                content += " Operation references were saved in this session. Recover using the original reference before attempting another append."
        if content and self._c.settings.numeric_verify_mode != "off":
            verification = verify_reply(
                content,
                list(outcome.tool_evidence),
                [turn.text, *turn.extraction_contexts],
            )
            if not verification.passed:
                logger.warning(
                    "Main chat numeric verification failed: %s", verification.ungrounded
                )
                if self._c.settings.numeric_verify_mode == "enforce":
                    content = "I could not verify the figures in the proposed answer. Check the recorded operation evidence before retrying any write."
        output_guard = await self._c.guardrails.validate_output(content)
        if not output_guard.passed:
            content = output_guard.rejection_message
        await self._c.db_client.save_message(
            turn.session_id, turn.user_id, "assistant", content
        )
        await self._c.db_client.update_session_timestamp(turn.session_id)
        self._remember(turn.user_id, turn.active_workbook_id, turn.text, content)
        return KlaudiaResponse(
            message=KlaudiaMessage(role="assistant", content=content),
            session_id=turn.session_id,
            processing_time_ms=int((time.time() - start) * 1000),
            tools_used=list(outcome.tools_called),
            metadata=turn.metadata,
            pending_approvals=list(outcome.pending_approvals),
            task_id=outcome.task_id,
            runtime="main",
            run_status=outcome.status,
            operation_references=list(outcome.operation_references),
            operation_receipts=list(outcome.operation_receipts),
        )

    @contextmanager
    def _approval_gate(self, user_id: int, session_id: int | None, scope: str | None):
        """Install the destructive-op gate and collect what it parks.

        Yields the list that receives one PendingApproval per refused
        operation, so the caller can hand the client its approve/reject
        buttons regardless of what the model says in prose.
        """
        parked: list[Any] = []
        service = self._c.approvals
        if service is None:
            yield parked
            return

        async def gate(tool_name: str, args: dict[str, Any], impact: Any) -> str:
            approval = await service.create(
                user_id=user_id,
                session_id=session_id,
                spreadsheet_id=scope,
                tool_name=tool_name,
                args=args,
                summary=impact.summary,
                rows_affected=impact.rows,
                columns_affected=impact.full_columns,
            )
            parked.append(approval)
            return approval.approval_id

        token = set_approval_gate(gate)
        try:
            yield parked
        finally:
            reset_approval_gate(token)

    async def _verify_numeric(
        self,
        content: str,
        tool_trace: list[tuple[str, dict, str]],
        extra_texts: list[str],
        allow_enforce: bool = True,
    ) -> str:
        """Gate monetary claims in the reply against tool-grounded values.

        Modes (NUMERIC_VERIFY_MODE): "off" skips; "log" flags ungrounded
        amounts and ships anyway; "enforce" additionally attempts ONE
        grounded rewrite and ships it only if it re-verifies. Fails open:
        verification errors never block the reply.
        """
        mode = self._c.settings.numeric_verify_mode
        if mode == "off" or not content:
            return content
        try:
            result = verify_reply(content, tool_trace, extra_texts)
        except Exception as exc:
            logger.error("Numeric verification crashed (fail-open): %s", exc)
            return content
        if result.passed:
            return content

        # Reply excerpt is logged because attributing a flag to its turn from
        # the results file alone proved unreliable (snippets are truncated),
        # and an unattributable flag cannot be triaged as true or false.
        logger.warning(
            "Numeric verification: ungrounded amounts %s (mode=%s, tool_calls=%d) "
            "reply=%r",
            result.ungrounded,
            mode,
            len(tool_trace),
            content[:160],
        )
        if self._langfuse is not None:
            try:
                with self._langfuse.span(
                    "klaudia.numeric_verify",
                    input={"ungrounded": result.ungrounded[:20], "mode": mode},
                    metadata={"tool_calls": len(tool_trace)},
                ):
                    pass
            except Exception:
                pass
        if mode != "enforce" or not allow_enforce:
            return content

        corrected = await self._c.supervisor.correct_numeric_claims(
            content, result.ungrounded, _format_evidence(tool_trace)
        )
        recheck = verify_reply(corrected, tool_trace, extra_texts)
        if recheck.passed:
            logger.info("Numeric enforcement: corrected reply verified")
            return corrected
        logger.error(
            "Numeric enforcement: rewrite still ungrounded %s; shipping original",
            recheck.ungrounded,
        )
        return content

    async def _recall_memory(self, user_id: int, query: str) -> str:
        """Long-term memory context for the system prompt (fail-soft, cheap).

        mem0 search = embed + pgvector scan; safe to run in the context gather.
        Returns "" when memory is disabled or the embed service is unreachable.
        """
        mem = self._c.memory
        if mem is None:
            return ""
        return await mem.recall(user_id, query)

    def _remember(
        self, user_id: int, spreadsheet_id: str | None, user_text: str, reply: str
    ) -> None:
        """Persist the turn to long-term memory in the background (write mode).

        The reply is already sent, so mem0's LLM extraction never adds latency
        to the response. Only active when MEMORY_MODE=write. The write runs
        inline (mem0.add here) or via Taskiq (enqueue, a worker runs it),
        selected by MEMORY_WRITE_MODE; either way it is backgrounded off the
        reply and tracked so drain_background can await it.
        """
        mem = self._c.memory
        if mem is None or self._c.settings.memory_mode != "write":
            return
        import asyncio

        if self._c.settings.memory_write_mode == "taskiq":
            coro = self._enqueue_memory(user_id, spreadsheet_id, user_text, reply)
        else:
            coro = mem.remember(user_id, spreadsheet_id, user_text, reply)
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _enqueue_memory(
        self, user_id: int, spreadsheet_id: str | None, user_text: str, reply: str
    ) -> None:
        """Enqueue a memory write to Taskiq (a worker runs mem0.add). Fail-soft:
        a dispatch error (e.g. Redis down) is logged, never raised into the turn.
        """
        try:
            from app.services.core.memory_tasks import persist_memory_task

            await persist_memory_task.kiq(
                user_id=user_id,
                spreadsheet_id=spreadsheet_id,
                user_text=user_text,
                assistant_text=reply,
            )
        except Exception as exc:
            logger.warning("Memory enqueue failed (fail-soft): %s", exc)

    async def drain_background(self) -> None:
        """Await in-flight background memory writes.

        The eval harness calls this before a fresh-session recall so a prior
        turn's write has landed (mem0 writes are async); it is also a clean hook
        for graceful shutdown so memory writes are not lost. No-op when memory
        is off (no tasks).
        """
        import asyncio

        pending = list(self._bg_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _recent_activity_context(self) -> str:
        """Deterministic cross-session continuity for the active spreadsheet.

        Reads the most-recently-edited sheets so a fresh session is not blank
        ("lanjut yang kemarin"). Ledger backend only: the gsheets backend has
        no per-sheet edit timestamps. Fails soft: any error yields an empty
        context rather than breaking the request.
        """
        service = self._c.spreadsheets
        scope = get_active_spreadsheet()
        if service is None or not scope:
            return ""
        try:
            activity = await service.recent_activity(scope, limit=3)
        except Exception as exc:
            logger.warning("Recent-activity read failed (fail-soft): %s", exc)
            return ""
        return build_continuity_context(activity)

    async def _resolve_scope(
        self, user_id: int, spreadsheet_id: str | None
    ) -> str | None:
        """Resolve the spreadsheet this request operates on.

        Returns None when per-user spreadsheets are off (gsheets backend):
        tools then fall through to the single default workspace.
        """
        if self._c.spreadsheets is None:
            return None
        return await self._c.spreadsheets.resolve_scope(user_id, spreadsheet_id)

    def _rejection_response(
        self, session_id: int, message: str, start: float
    ) -> KlaudiaResponse:
        elapsed = int((time.time() - start) * 1000)
        return KlaudiaResponse(
            message=KlaudiaMessage(role="assistant", content=message),
            session_id=session_id,
            processing_time_ms=elapsed,
            tools_used=[],
            runtime=self._c.settings.chat_runtime,
            run_status="rejected" if self._c.settings.chat_runtime == "main" else None,
        )

    async def _check_queue_pressure(self) -> str | None:
        """PRV: read queue depth from Redis. Reject if at hard cap, warn at soft.

        Returns rejection message if hard cap exceeded; otherwise None.
        Soft-cap warnings are emitted as SSE events by the streaming caller
        (we don't have a yield context here). In sync mode the soft cap is
        ignored — the queue is empty since extraction runs inline.
        """
        settings = self._c.settings
        if settings.extraction_mode != "async":
            return None
        cache = self._c.dedup_cache
        if cache is None or not hasattr(cache, "queue_depth"):
            return None
        try:
            depth = await cache.queue_depth(settings.taskiq_queue_name)
        except Exception:
            return None
        if depth >= settings.extraction_queue_depth_reject:
            logger.warning("Queue depth %d exceeds reject cap; rejecting upload", depth)
            return f"Sistem lagi sibuk (antrian {depth} task). Coba lagi sebentar ya."
        return None

    async def _run_extraction_stream(
        self,
        attachments: list[FileAttachment],
        session_id: int,
        user_id: int,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run extraction for each attachment, yielding SSE events as pages
        complete. Final yield carries `__final__: True` and `payload`: the
        list of extraction_data dicts the caller injects into llm_messages.
        """
        settings = self._c.settings
        mode = settings.extraction_mode

        if mode == "async":
            async for event in self._run_extraction_async(
                attachments, session_id, user_id
            ):
                yield event
            return

        # Sync mode: each attachment finishes inline, emit one extraction event each.
        results: list[dict[str, Any]] = []
        for att in attachments:
            yield {
                "type": "extraction",
                "data": {"status": "processing", "file_name": att.filename},
            }
            result = await self._extraction_agent.process(att, session_id, user_id)
            payload = {
                "file_id": result.file_id,
                "file_name": result.file_name,
                "pages": result.pages,
                "status": result.status,
                "summary": result.summary,
            }
            results.append(payload)
            yield {"type": "extraction", "data": payload}
        yield {"__final__": True, "payload": results}

    async def _run_extraction_async(
        self,
        attachments: list[FileAttachment],
        session_id: int,
        user_id: int,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async path: enqueue per-page tasks, subscribe to pubsub, build the
        final extraction_results list from DB once all pages report.
        """
        from app.exceptions import IngestRejectedError
        from app.services.extraction.queue.progress import stream_progress

        settings = self._c.settings
        cache = self._c.dedup_cache
        ingest = self._c.ingest_service

        enqueued: list[Any] = []  # EnqueuedFile
        for att in attachments:
            yield {
                "type": "extraction",
                "data": {"status": "queueing", "file_name": att.filename},
            }
            try:
                ef = await ingest.enqueue(att, session_id=session_id, user_id=user_id)
            except IngestRejectedError as e:
                yield {
                    "type": "extraction",
                    "data": {
                        "status": "rejected",
                        "file_name": att.filename,
                        "reason": e.reason,
                        "message": str(e),
                    },
                }
                continue
            enqueued.append(ef)
            yield {
                "type": "extraction",
                "data": {
                    "status": "queued",
                    "file_id": ef.file_id,
                    "file_name": ef.file_name,
                    "pages_total": len(ef.pages),
                    "queued": ef.queued,
                    "cached_hits": ef.cached_hits,
                },
            }

        if not enqueued:
            yield {"__final__": True, "payload": []}
            return

        # Wait for queued pages via pubsub. Cache hits are already in the DB
        # by now — we only subscribe for files that have queued pages > 0.
        file_pages = {ef.file_id: ef.queued for ef in enqueued if ef.queued > 0}
        timeout = settings.extraction_page_timeout_seconds * max(
            (max(file_pages.values()) if file_pages else 1), 1
        )

        if file_pages and cache is not None and hasattr(cache, "client"):
            async for event in stream_progress(
                cache.client,  # type: ignore[attr-defined]
                file_pages=file_pages,
                timeout_seconds=float(timeout),
            ):
                yield {
                    "type": "extraction",
                    "data": {
                        "status": "page_done",
                        "file_id": event.file_id,
                        "page": event.page,
                        "page_status": event.status,
                        "cache_layer": event.cache_layer,
                        "error": event.error,
                    },
                }

        # Build final extraction_results from DB so supervisor sees the same
        # shape sync mode produces.
        results: list[dict[str, Any]] = []
        for ef in enqueued:
            pages_rows = await self._c.db_client.fetchall(
                "SELECT page, agent_extracted, status FROM pages "
                "WHERE metadata_file_id = $1 ORDER BY page",
                (ef.file_id,),
            )
            file_row = await self._c.db_client.fetchone(
                "SELECT status, status_message FROM metadata_file WHERE id = $1",
                (ef.file_id,),
            )
            import json as _json

            pages_payload = []
            for r in pages_rows:
                ext = {}
                if r["agent_extracted"]:
                    try:
                        ext = _json.loads(r["agent_extracted"])
                    except _json.JSONDecodeError:
                        pass
                pages_payload.append(
                    {
                        "page": r["page"],
                        "extraction": ext,
                        "status": r["status"],
                    }
                )
            payload = {
                "file_id": ef.file_id,
                "file_name": ef.file_name,
                "pages": pages_payload,
                "status": file_row["status"] if file_row else "completed",
                "summary": f"File {ef.file_name}: {len(pages_payload)} page(s)",
            }
            results.append(payload)
            yield {"type": "extraction", "data": payload}

        # Final aggregate status update on metadata_file (workers don't track
        # the per-file roll-up).
        for ef, payload in zip(enqueued, results):
            page_statuses = [p["status"] for p in payload["pages"]]
            ok = sum(1 for s in page_statuses if s == "extracted")
            total = len(page_statuses)
            if total == 0:
                final_status = "failed"
                msg = "no pages persisted"
            elif ok == total:
                final_status = "completed"
                msg = f"All {total} page(s) extracted"
            elif ok == 0:
                final_status = "failed"
                msg = "All pages failed"
            else:
                final_status = "partial"
                msg = f"{ok}/{total} page(s) extracted"
            await self._c.db_client.execute(
                "UPDATE metadata_file SET status = $1, status_message = $2 WHERE id = $3",
                (final_status, msg, ef.file_id),
            )
            payload["status"] = final_status

        yield {"__final__": True, "payload": results}
