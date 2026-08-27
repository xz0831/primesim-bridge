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


EXIT_CODES_HSPICE: Dict[int, str] = {
    0: "Simulation succeeded",
    1: "Simulation failed due to errors",
    2: "PrimeSim HSPICE stopped due to lack of license",
    3: "Interrupted (Ctrl+\\)",
    6: "Aborted (SIGABRT, e.g. out of memory)",
    8: "Floating-point exception",
    11: "Segmentation fault",
    15: "Terminated (UNIX kill)",
    24: "CPU time limit exceeded",
    28: "No space left on device (simulation cannot start)",
    38: "Error writing to output file (simulation started)",
    99: "Error during -dp distribution",
    101: "Interrupted (Ctrl+C)",
}


def classify_exit(returncode: int) -> Tuple[ExecutionStatus, Optional[str]]:
    if returncode == 0:
        return ExecutionStatus.SUCCESS, None
    return ExecutionStatus.FAILURE, EXIT_CODE_TABLE.get(
        returncode, f"exit code {returncode}"
    )


def classify_exit_hspice(returncode: int) -> Tuple[ExecutionStatus, Optional[str]]:
    code = (
        -returncode
        if returncode < 0
        else returncode - 128
        if 128 < returncode < 160
        else returncode
    )
    if code == 0:
        return ExecutionStatus.SUCCESS, None
    return ExecutionStatus.FAILURE, EXIT_CODES_HSPICE.get(code, f"exit code {code}")
