"""Bounded error locations without SQL parameters, credentials or locals."""
from pathlib import Path
import traceback


def exception_trace(exc: BaseException) -> list[dict]:
    root = Path(__file__).resolve().parents[1]
    chain = []
    seen = set()
    while exc is not None and id(exc) not in seen and len(chain) < 4:
        seen.add(id(exc))
        frames = []
        for frame in traceback.extract_tb(exc.__traceback__)[-16:]:
            path = Path(frame.filename)
            try:
                name = path.resolve().relative_to(root).as_posix()
            except ValueError:
                name = path.name
            frames.append({"file": name, "line": frame.lineno, "function": frame.name})
        chain.append({"error": type(exc).__name__, "frames": frames})
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return chain
