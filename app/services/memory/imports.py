"""Explicitly reviewed preference imports without access to external memory stores."""

import json
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    model_validator,
)

from app.services.memory.contracts import DocumentEdit, MAX_EXPECTED_REVISION

SourceId = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
]


class PreferenceImport(BaseModel):
    """Accept a reviewed replacement and bounded, caller-declared source IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_revision: Annotated[
        int, Field(strict=True, ge=0, le=MAX_EXPECTED_REVISION)
    ]
    content: Annotated[str, AfterValidator(DocumentEdit.bound_content)]
    source_ids: Annotated[list[SourceId], Field(min_length=1, max_length=5)]
    reviewed_preferences_only: StrictBool

    @model_validator(mode="after")
    def require_review(self) -> "PreferenceImport":
        """Require explicit human review and distinct source records.

        Returns:
            Validated import declaration.

        Raises:
            ValueError: Review is absent, text is blank or source identities repeat.
        """
        if not self.reviewed_preferences_only or not self.content.strip():
            raise ValueError(
                "Review the complete replacement as preferences only before importing"
            )
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("Import source IDs must be unique")
        return self

    def as_edit(self) -> DocumentEdit:
        """Build a normal checked document edit with bounded source provenance.

        Returns:
            Exact reviewed text and declared source IDs, without accounting policy.
        """
        return DocumentEdit(
            expected_revision=self.expected_revision,
            content=self.content,
            source_note="Reviewed mem0 preference import: "
            + json.dumps(self.source_ids, separators=(",", ":")),
        )
