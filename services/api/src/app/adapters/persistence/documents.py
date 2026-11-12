"""Retain supported imports from the installed service."""

from klaudia_api.adapters.persistence.documents import Any as Any
from klaudia_api.adapters.persistence.documents import DocumentPage as DocumentPage
from klaudia_api.adapters.persistence.documents import FileUpload as FileUpload
from klaudia_api.adapters.persistence.documents import PageOutcome as PageOutcome
from klaudia_api.adapters.persistence.documents import (
    PostgresDocumentStore as PostgresDocumentStore,
)
from klaudia_api.adapters.persistence.documents import asyncpg as asyncpg
from klaudia_api.adapters.persistence.documents import json as json
