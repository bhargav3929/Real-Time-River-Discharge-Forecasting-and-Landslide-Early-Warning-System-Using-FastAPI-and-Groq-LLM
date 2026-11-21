"""One error type that always renders as {"error": "...", "code": "..."}."""


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def body(self) -> dict:
        return {"error": self.message, "code": self.code}
