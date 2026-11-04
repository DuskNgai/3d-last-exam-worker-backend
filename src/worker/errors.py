"""Worker API errors carrying the contract status code and error code."""


class ApiError(Exception):
    """One Master request cannot be served as sent."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)

        self.status_code = status_code
        self.code = code
        self.message = message
