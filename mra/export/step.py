"""STEP AP214/AP242 export (and import, used for round-trip testing).

Only analytic B-Rep shapes pass through here; the pipeline never exports
tessellated geometry.
"""

from __future__ import annotations

from pathlib import Path

from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Interface import Interface_Static
from OCP.STEPControl import (
    STEPControl_AsIs,
    STEPControl_Reader,
    STEPControl_Writer,
)
from OCP.TopoDS import TopoDS_Shape

_SCHEMAS = {
    "AP214": "AP214CD",
    "AP242": "AP242DIS",
}


def export_step(
    shape: TopoDS_Shape, path: str | Path, schema: str = "AP242"
) -> None:
    """Write ``shape`` to a STEP file.

    Args:
        shape: A valid analytic solid.
        path: Destination file path (.step / .stp).
        schema: "AP214" or "AP242" (default — current standard).

    Raises:
        ValueError: On unknown schema.
        RuntimeError: When the writer fails.
    """
    if schema not in _SCHEMAS:
        raise ValueError(f"schema must be one of {sorted(_SCHEMAS)}")
    Interface_Static.SetCVal_s("write.step.schema", _SCHEMAS[schema])
    writer = STEPControl_Writer()
    status = writer.Transfer(shape, STEPControl_AsIs)
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"STEP transfer failed: {status}")
    status = writer.Write(str(path))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed: {status}")


def import_step(path: str | Path) -> TopoDS_Shape:
    """Read the first shape from a STEP file (round-trip verification).

    Raises:
        FileNotFoundError: When the file does not exist.
        RuntimeError: When reading fails.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"STEP read failed: {status}")
    reader.TransferRoots()
    return reader.OneShape()
