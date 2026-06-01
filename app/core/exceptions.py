from fastapi import HTTPException, status


class AppBaseError(Exception):
    """Base for all domain errors that map directly to HTTP responses.

    Subclasses set ``status_code`` and ``error`` as class variables; the
    generic exception handler in main.py serialises them to JSON.
    """

    status_code: int = 500
    error: str = "internal_error"


class ScraperError(Exception):
    def __init__(self, source: str, message: str) -> None:
        self.source = source
        super().__init__(f"[{source}] {message}")


class NormalizationError(Exception):
    pass


class DeduplicationError(Exception):
    pass


class JobNotFoundError(HTTPException):
    def __init__(self, job_id: int) -> None:
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")


class RateLimitError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
