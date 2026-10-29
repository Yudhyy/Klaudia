"""Compatibility imports for the canonical ledger_core.resources module."""

from ledger_core.resources import (
    Annotated as Annotated,
    BaseModel as BaseModel,
    ConfigDict as ConfigDict,
    Field as Field,
    Name as Name,
    ResourceExistsError as ResourceExistsError,
    ResourceInspection as ResourceInspection,
    ResourceNotFoundError as ResourceNotFoundError,
    ResourceSearch as ResourceSearch,
    StrictInt as StrictInt,
    TableRegistration as TableRegistration,
    TableUpdate as TableUpdate,
    date as date,
    model_validator as model_validator,
)
