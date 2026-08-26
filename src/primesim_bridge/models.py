from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILURE = "FAILURE"


class SimulationResult(BaseModel):
    status: ExecutionStatus
    data: Dict[str, Any]
    errors: List[str]
    warnings: List[str]
    metadata: Dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status is ExecutionStatus.SUCCESS


EXIT_CODE_TABLE: Dict[int, str] = {
    0: "Simulation succeeded",
    1: "Memory related error",
    2: "File related error",
    3: "Not supported yet",
    4: "Input argument error",
    5: "General parsing error",
    6: "I/O file parsing error",
    7: "SPF file parsing error",
    8: "General elaboration error",
    9: "Unable to resolve expression",
    10: "Option related error",
    11: "Netlist connection error",
    12: "Matrix related error",
    13: "Unable to converge",
    14: "Output related error",
    15: "License related error",
    16: "Unable to reinvoke by execvp()",
    17: "Parallel matrix error",
    18: "ADFMI related error",
    19: "S-element module error",
    20: "B-element module error",
    21: "Monte Carlo module error",
    22: "Verilog-A error",
    23: "TMI2 error",
    24: "Z-Transform module error",
    25: "ETMI SOA error",
    28: "PrimeSim API multiple analysis statements error",
    29: "PDMI related error",
    30: "Voltage Loop error",
    31: "Obsolete Option error (PrimeSim API)",
    32: "Bisection error",
    33: "TNA error",
    34: "DC not converged (only when primesim_exit_dc_fail set)",
}


def classify_exit(returncode: int) -> Tuple[ExecutionStatus, Optional[str]]:
    if returncode == 0:
        return ExecutionStatus.SUCCESS, None
    return ExecutionStatus.FAILURE, EXIT_CODE_TABLE.get(
        returncode, f"exit code {returncode}"
    )
