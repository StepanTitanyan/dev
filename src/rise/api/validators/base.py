from abc import ABC, abstractmethod


class BaseValidator(ABC):
    """
    Each lender implements this. validate() receives the raw request dict
    and returns a normalised payload dict, or raises a Pydantic ValidationError.
    worker_type identifies which worker processes this application and which
    SQS queue it is routed to.
    """

    worker_type: str

    @abstractmethod
    def validate(self, raw: dict) -> dict:
        pass
