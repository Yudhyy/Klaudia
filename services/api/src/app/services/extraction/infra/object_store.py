"""Retain supported imports from the installed service."""

from klaudia_api.adapters.documents.object_store.s3 import (
    AsyncIterator as AsyncIterator,
)
from klaudia_api.adapters.documents.object_store.s3 import ClientError as ClientError
from klaudia_api.adapters.documents.object_store.s3 import MinIOClient as MinIOClient
from klaudia_api.adapters.documents.object_store.s3 import (
    ObjectStoreError as ObjectStoreError,
)
from klaudia_api.adapters.documents.object_store.s3 import Settings as Settings
from klaudia_api.adapters.documents.object_store.s3 import StoredObject as StoredObject
from klaudia_api.adapters.documents.object_store.s3 import aioboto3 as aioboto3
from klaudia_api.adapters.documents.object_store.s3 import (
    asynccontextmanager as asynccontextmanager,
)
from klaudia_api.adapters.documents.object_store.s3 import dataclass as dataclass
from klaudia_api.adapters.documents.object_store.s3 import logger as logger
from klaudia_api.adapters.documents.object_store.s3 import logging as logging
from klaudia_api.adapters.documents.object_store.s3 import shard_path as shard_path
