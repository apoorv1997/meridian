"""Document event schema for the meridian.documents Kafka topic."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentEventType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class DocumentEvent(BaseModel):
    """A document lifecycle event consumed from meridian.documents.

    Producers (user apps, ingestion APIs) publish these; the RAG engine
    consumer chunks + embeds on create/update and removes chunks on delete.
    """

    type: DocumentEventType
    document_id: str
    tenant_id: str
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def requires_content(self) -> bool:
        return self.type in (DocumentEventType.CREATE, DocumentEventType.UPDATE)
