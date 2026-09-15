"""The perception-and-action adapter between the agent and a live browser.

``surface.base`` holds pure types and the ``Surface`` protocol (no browser, no
model SDK). ``surface.web`` is the only module in the whole project that imports
``playwright``. Import ``web`` lazily so that merely importing ``surface`` (or
``surface.base``) never requires Playwright to be installed.
"""

from surface.base import (
    Action,
    ActionResult,
    Click,
    Done,
    Escalate,
    EvidenceRef,
    GiveUp,
    NamedRegion,
    Navigate,
    Node,
    Observation,
    Read,
    ResolutionTrace,
    Select,
    Surface,
    TableContext,
    Type,
    action_function_declarations,
    ACTION_CLASSES,
    ACTION_KINDS,
)

__all__ = [
    "Action",
    "ActionResult",
    "Click",
    "Done",
    "Escalate",
    "EvidenceRef",
    "GiveUp",
    "NamedRegion",
    "Navigate",
    "Node",
    "Observation",
    "Read",
    "ResolutionTrace",
    "Select",
    "Surface",
    "TableContext",
    "Type",
    "action_function_declarations",
    "ACTION_CLASSES",
    "ACTION_KINDS",
]
